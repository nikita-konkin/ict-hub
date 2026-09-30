"""
converters.py — Behaviour that differs between converters.

registry.py describes each converter's image and CLI flags (data). This module
holds the per-converter logic around that data:

  - page_context()  → extra template variables for run.html, e.g. the data
                      trees a converter's folder pickers are built from
  - prepare_form()  → validates a submitted run form and fills in the
                      server-side paths, which never come from the browser

Supporting a new converter means a registry entry plus, if it needs either
behaviour, one function per hook registered in _PAGE_CONTEXT / _PREPARE_FORM.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi.concurrency import run_in_threadpool
from markupsafe import Markup
from sqlalchemy.orm import Session

from app import config as cfg
from app.data_indexer_client import (
    list_parquet_output_structure_async,
    list_rinex_server_structure_async,
    list_tecsuite_output_structure_async,
)
from app.models import JobRun
from app.runner import ensure_container_running

logger = logging.getLogger(__name__)

FormData = dict[str, Any]
Tree = list[dict[str, object]]


class FormError(Exception):
    """A submitted run form cannot be run; the message is shown to the user.

    Plain-string messages are escaped when rendered; pass Markup for deliberate markup.
    """

    def __init__(self, message: str | Markup, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedRun:
    """What prepare_form worked out besides the form fields themselves."""

    input_note: str = ""  # stored as JobRun.rinex_path; empty → the form's "root"
    output_path: str = ""  # stored as JobRun.output_path; empty → the form's "out"
    notice: str = ""  # shown above the job panel


def is_truthy_checkbox(value: object) -> bool:
    """Interpret common HTML checkbox encodings as booleans."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"on", "true", "1", "yes"}


def _join_host_path(base_path: str, suffix: str) -> str:
    """Join host path with '/YYYY[/DDD]' suffix while preserving base style."""
    clean_suffix = str(suffix or "").strip().replace("\\", "/").strip("/")
    if not clean_suffix:
        return base_path
    return base_path.rstrip("/\\") + "/" + clean_suffix


def _scan_path(container_path: str, host_path: str) -> str:
    """Where the indexer finds a data root: its container mount, else the host path."""
    return container_path.strip() or host_path.strip()


async def _no_tree() -> Tree:
    return []


def _env_note(env_name: str) -> str:
    return f"Configured from environment variable {env_name}"


# ─────────────────────────────────────────────────────────────────────────────
# TEC-Suite
# ─────────────────────────────────────────────────────────────────────────────

# Accept both layouts used by TEC-Suite input data:
# - /YYYY_original[/DDD]             (day directly under year, 2-3 digits)
# - /YYYY_original/MM/DD             (month/day under year, 2-3 digits each)
_TECSUITE_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}_original(?:/\d{2,3}){0,2}$")


async def _tec_suite_page() -> dict[str, object]:
    host_path = cfg.RINEX_DATA_PATH_HOST
    scan_path = cfg.RINEX_DATA_PATH_CONTAINER or host_path
    return {
        "tec_rinex_host_path": host_path,
        "tec_rinex_scan_path": scan_path,
        "tec_rinex_tree": await list_rinex_server_structure_async(scan_path) if scan_path else [],
    }


async def _prepare_tec_suite(form: FormData, db: Session) -> PreparedRun:
    root_host = cfg.RINEX_DATA_PATH_HOST.strip()
    if not root_host:
        raise FormError("RINEX_DATA_PATH_HOST is not configured.")
    if not _TECSUITE_ROOT_SUBPATH_RE.fullmatch(str(form.get("root_subpath", "")).strip()):
        raise FormError("Select a valid year/day folder before running TEC-Suite.")
    form["root"] = root_host
    return PreparedRun(input_note=_env_note("RINEX_DATA_PATH_HOST"))


# ─────────────────────────────────────────────────────────────────────────────
# AbsTEC Suite
# ─────────────────────────────────────────────────────────────────────────────


async def _abstec_page() -> dict[str, object]:
    scan_path = _scan_path(cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER, cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST)
    return {"abstec_dat_tree": await list_tecsuite_output_structure_async(scan_path) if scan_path else []}


async def _prepare_abstec(form: FormData, db: Session) -> PreparedRun:
    dat_root_host = cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip()
    if not dat_root_host:
        raise FormError("TECSUITE_OUT_DAT_DATA_PATH_HOST is not configured.")
    form["dat_path"] = dat_root_host
    form["output_dir"] = cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip()
    # Batch mode auto-discovers stations per day; run_absoltec rejects
    # --days combined with --site, so fail fast with a friendly message.
    if str(form.get("days", "")).strip() and str(form.get("site", "")).strip():
        raise FormError(
            "Days (batch mode) cannot be combined with a Site "
            "selection — batch runs auto-discover stations for each day. Clear the Site "
            "selection, or use Day Of Year (single run) together with Site."
        )
    notice = ""
    if str(form.get("runner", "")).strip() == "dockur":
        notice = await _prepare_dockur_runner(db)
    return PreparedRun(input_note=_env_note("TECSUITE_OUT_DAT_DATA_PATH_HOST"), notice=notice)


async def _prepare_dockur_runner(db: Session) -> str:
    """Check the dockur XP runner can take a job; returns a notice for the user, if any."""
    # The XP guest executes jobs one at a time from a single shared queue:
    # parallel dockur runs gain nothing, blow through timeouts while queued,
    # and can cross-rename same-prefix station outputs from the shared out
    # folder. Allow only one dockur job at a time.
    running_abstec = db.query(JobRun).filter(JobRun.converter == "abstec-suite", JobRun.status == "running").all()
    if any(j.flags.get("runner") == "dockur" for j in running_abstec):
        raise FormError(
            "Another AbsTEC dockur job is already "
            "running. The Windows XP VM processes jobs strictly one at a time from a "
            "single queue, so a parallel dockur run would only sit in the queue, hit "
            "its execution timeout, and risk mixing outputs of stations that share a "
            "4-character prefix. Wait for the running job to finish or stop it first."
        )
    # The guest watcher only runs while the XP VM container is up, so
    # a stopped VM would silently burn the whole execution timeout.
    # Start it here if it exists but is not running.
    vm_name = cfg.ABSTEC_DOCKUR_VM_CONTAINER.strip()
    try:
        vm_state = await run_in_threadpool(ensure_container_running, vm_name)
    except Exception as exc:
        logger.error("Failed to start XP VM container %r: %s", vm_name, exc)
        raise FormError(f"Could not start the Windows XP VM container '{vm_name}': {exc}", 500) from exc
    if vm_state == "not_found":
        raise FormError(
            Markup(
                "The Windows XP VM container '{}' does not exist, so no guest is "
                "available to execute dockur jobs. Create it first from the "
                "abstec-suite folder with "
                "<code>docker compose -f docker-compose.dockur.yml up -d abstec-xp</code> "
                "(first boot installs XP and takes 10&ndash;20 minutes; watch port 8006)."
            ).format(vm_name)
        )
    if vm_state == "started":
        logger.info("XP VM container %r was stopped; started it for this job", vm_name)
        return (
            f"The Windows XP VM container '{vm_name}' was stopped and has been "
            "started automatically. Windows XP needs a minute or two to boot "
            "before its job watcher picks up the first station, so the first "
            "run takes correspondingly longer — make sure the execution "
            "timeout has enough headroom."
        )
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# DAT <-> Parquet
# ─────────────────────────────────────────────────────────────────────────────

_DAT_PARQUET_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}(?:/\d{1,3})?$")
_DEFAULT_DIRECTION = "dat-to-parquet"
_DIRECTIONS = ("dat-to-parquet", "parquet-to-dat")


def _profile(label: str, src_env: str, dst_env: str) -> dict[str, str]:
    return {
        "label": label,
        "src": str(getattr(cfg, src_env)).strip(),
        "dst": str(getattr(cfg, dst_env)).strip(),
        "src_env": src_env,
        "dst_env": dst_env,
        "source_note": _env_note(src_env),
    }


def dat_parquet_profiles(direction: str) -> dict[str, dict[str, str]]:
    """Env-backed source/destination profiles for one conversion direction."""
    if direction == "parquet-to-dat":
        return {
            "tecsuite": _profile(
                "TEC-Suite parquet output", "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "TECSUITE_OUT_DAT_DATA_PATH_HOST"
            ),
            "abstec": _profile(
                "AbsTEC parquet output", "PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST", "ABSTEC_OUTPUT_DATA_PATH_HOST"
            ),
        }
    return {
        "tecsuite": _profile(
            "TEC-Suite DAT output", "TECSUITE_OUT_DAT_DATA_PATH_HOST", "PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST"
        ),
        "abstec": _profile("AbsTEC output", "ABSTEC_OUTPUT_DATA_PATH_HOST", "PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST"),
    }


def _reduce_to_year_days(dat_tree: Tree) -> Tree:
    """Keep only year/day values for compact UI payloads."""
    reduced: Tree = []
    for year_item in dat_tree:
        year_name = str(year_item.get("year", "")).strip()
        if not year_name:
            continue
        days_raw = year_item.get("days", [])
        days: list[str] = []
        if isinstance(days_raw, list):
            for day_item in days_raw:
                if isinstance(day_item, dict):
                    day_name = str(day_item.get("day", "")).strip()
                    if day_name:
                        days.append(day_name)
        reduced.append({"year": year_name, "days": days})
    return reduced


async def _dat_parquet_page() -> dict[str, object]:
    dat_tecsuite = _scan_path(cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER, cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST)
    dat_abstec = _scan_path(cfg.ABSTEC_OUTPUT_DATA_PATH_CONTAINER, cfg.ABSTEC_OUTPUT_DATA_PATH_HOST)
    parquet_tecsuite = _scan_path(
        cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST
    )
    parquet_abstec = _scan_path(cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER, cfg.PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST)

    tecsuite_tree, abstec_tree, parquet_tecsuite_tree, parquet_abstec_tree = await asyncio.gather(
        list_tecsuite_output_structure_async(dat_tecsuite) if dat_tecsuite else _no_tree(),
        # AbsTEC output is a plain YYYY/DDD tree, which the parquet listing handles.
        list_parquet_output_structure_async(dat_abstec) if dat_abstec else _no_tree(),
        list_parquet_output_structure_async(parquet_tecsuite) if parquet_tecsuite else _no_tree(),
        list_parquet_output_structure_async(parquet_abstec) if parquet_abstec else _no_tree(),
    )
    return {
        "dat_parquet_source_tree_matrix": {
            "dat-to-parquet": {"tecsuite": _reduce_to_year_days(tecsuite_tree), "abstec": abstec_tree},
            "parquet-to-dat": {"tecsuite": parquet_tecsuite_tree, "abstec": parquet_abstec_tree},
        },
    }


def _parse_day(raw: str, flag: str) -> int | None:
    """Validate an optional day-of-year field (1..366)."""
    if not raw:
        return None
    if not raw.isdigit():
        raise FormError(f"{flag} must be an integer in range 1..366.")
    day = int(raw)
    if not 1 <= day <= 366:
        raise FormError(f"{flag} must be in range 1..366.")
    return day


async def _prepare_dat_parquet(form: FormData, db: Session) -> PreparedRun:
    direction = str(form.get("direction", _DEFAULT_DIRECTION)).strip() or _DEFAULT_DIRECTION
    profile_name = str(form.get("dataset_profile", "tecsuite")).strip() or "tecsuite"
    overwrite = is_truthy_checkbox(form.get("overwrite", False))
    root_subpath = str(form.get("root_subpath", "")).strip()

    profile = dat_parquet_profiles(direction).get(profile_name)
    if profile is None:
        raise FormError(f"Select a valid DAT <-> Parquet source profile for direction '{direction}'.")
    if not profile["src"]:
        raise FormError(f"{profile['src_env']} is not configured.")
    # Overwrite converts in place: the source directory is also the destination.
    dst_root = profile["src"] if overwrite else profile["dst"]
    if not dst_root:
        raise FormError(f"{profile['dst_env']} is not configured.")

    day_from = _parse_day(str(form.get("day_from", "")).strip(), "--day-from")
    day_to = _parse_day(str(form.get("day_to", "")).strip(), "--day-to")
    if day_from is not None and day_to is not None and day_from > day_to:
        raise FormError("--day-from must be less than or equal to --day-to.")
    if day_from is not None:
        form["day_from"] = day_from
    if day_to is not None:
        form["day_to"] = day_to

    if root_subpath and not _DAT_PARQUET_ROOT_SUBPATH_RE.fullmatch(root_subpath):
        raise FormError("Select a valid year/day folder before running DAT <-> Parquet.")
    form["src"] = _join_host_path(profile["src"], root_subpath)
    # Mount the destination root and let the converter create nested
    # year/day folders inside it. Binding the leaf subdirectory directly
    # causes Docker to create/chown it on the host before startup, which
    # can fail on protected mounts even though writing below the root is
    # otherwise permitted.
    form["dst"] = dst_root
    form["dst_subpath"] = root_subpath
    form["dst_effective"] = _join_host_path(dst_root, root_subpath)
    form["dataset_profile"] = profile_name
    form["root_subpath"] = root_subpath
    return PreparedRun(input_note=profile["source_note"], output_path=form["dst_effective"])


# ─────────────────────────────────────────────────────────────────────────────
# Hooks
# ─────────────────────────────────────────────────────────────────────────────

_PAGE_CONTEXT: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
    "tec-suite": _tec_suite_page,
    "abstec-suite": _abstec_page,
    "dat-parquet-handler": _dat_parquet_page,
}

_PREPARE_FORM: dict[str, Callable[[FormData, Session], Awaitable[PreparedRun]]] = {
    "tec-suite": _prepare_tec_suite,
    "abstec-suite": _prepare_abstec,
    "dat-parquet-handler": _prepare_dat_parquet,
}


def _default_page_context() -> dict[str, Any]:
    """Variables run.html expects on every converter page."""
    return {
        "tec_rinex_host_path": "",
        "tec_rinex_tree": [],
        "tec_rinex_scan_path": cfg.RINEX_DATA_PATH_CONTAINER,
        "abstec_dat_tree": [],
        "abstec_dat_host_path": cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST,
        "abstec_dat_scan_path": cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER or cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST,
        "abstec_output_host_path": cfg.ABSTEC_OUTPUT_DATA_PATH_HOST,
        "dat_parquet_default_direction": _DEFAULT_DIRECTION,
        "dat_parquet_profiles": dat_parquet_profiles(_DEFAULT_DIRECTION),
        "dat_parquet_profile_matrix": {direction: dat_parquet_profiles(direction) for direction in _DIRECTIONS},
        "dat_parquet_source_tree_matrix": {direction: {"tecsuite": [], "abstec": []} for direction in _DIRECTIONS},
    }


async def page_context(converter_name: str) -> dict[str, Any]:
    """Template variables for a converter's run page."""
    context = _default_page_context()
    hook = _PAGE_CONTEXT.get(converter_name)
    if hook is not None:
        context.update(await hook())
    return context


async def prepare_form(converter_name: str, form: FormData, db: Session) -> PreparedRun:
    """
    Validate a run form and fill in server-side paths, in place.

    Raises FormError when the form or the server configuration does not allow
    the run.
    """
    hook = _PREPARE_FORM.get(converter_name)
    if hook is None:
        return PreparedRun()
    return await hook(form, db)
