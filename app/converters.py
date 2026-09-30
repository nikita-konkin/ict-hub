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
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app import config as cfg
from app.data_indexer_client import (
    list_parquet_output_structure_async,
    list_rinex_server_structure_async,
    list_tecsuite_output_structure_async,
)

FormData = MutableMapping[str, Any]
Tree = list[dict[str, object]]


class FormError(ValueError):
    """A submitted run form is invalid; the message is shown to the user."""


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
    return f"{base_path.rstrip('/\\')}/{clean_suffix}"


def _scan_path(container_path: str, host_path: str) -> str:
    """Where the indexer finds a data root: its container mount, else the host path."""
    return container_path.strip() or host_path.strip()


async def _no_tree() -> Tree:
    return []


# ─────────────────────────────────────────────────────────────────────────────
# TEC-Suite
# ─────────────────────────────────────────────────────────────────────────────

_TECSUITE_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}_original(?:/\d{2,3})?$")
_TECSUITE_ENV_ROOT_NOTE = "Configured from environment variable RINEX_DATA_PATH_HOST"


async def _tec_suite_page() -> dict[str, object]:
    host_path = cfg.RINEX_DATA_PATH_HOST
    scan_path = cfg.RINEX_DATA_PATH_CONTAINER or host_path
    return {
        "tec_rinex_host_path": host_path,
        "tec_rinex_scan_path": scan_path,
        "tec_rinex_tree": await list_rinex_server_structure_async(scan_path) if scan_path else [],
    }


def _prepare_tec_suite(form: FormData) -> str:
    root_host = cfg.RINEX_DATA_PATH_HOST.strip()
    if not root_host:
        raise FormError("RINEX_DATA_PATH_HOST is not configured.")
    if not _TECSUITE_ROOT_SUBPATH_RE.fullmatch(str(form.get("root_subpath", "")).strip()):
        raise FormError("Select a valid year/day folder before running TEC-Suite.")
    form["root"] = root_host
    return _TECSUITE_ENV_ROOT_NOTE


# ─────────────────────────────────────────────────────────────────────────────
# AbsTEC Suite
# ─────────────────────────────────────────────────────────────────────────────

_ABSTEC_ENV_INPUT_NOTE = "Configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST"


async def _abstec_page() -> dict[str, object]:
    scan_path = _scan_path(cfg.TECSUITE_OUT_DAT_DATA_PATH_CONTAINER, cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST)
    return {"abstec_dat_tree": await list_tecsuite_output_structure_async(scan_path) if scan_path else []}


def _prepare_abstec(form: FormData) -> str:
    dat_root_host = cfg.TECSUITE_OUT_DAT_DATA_PATH_HOST.strip()
    if not dat_root_host:
        raise FormError("TECSUITE_OUT_DAT_DATA_PATH_HOST is not configured.")
    form["dat_path"] = dat_root_host
    form["output_dir"] = cfg.ABSTEC_OUTPUT_DATA_PATH_HOST.strip()
    return _ABSTEC_ENV_INPUT_NOTE


# ─────────────────────────────────────────────────────────────────────────────
# DAT <-> Parquet
# ─────────────────────────────────────────────────────────────────────────────

_DAT_PARQUET_ROOT_SUBPATH_RE = re.compile(r"^/\d{4}(?:/\d{1,3})?$")
_DEFAULT_DIRECTION = "dat-to-parquet"
_DIRECTIONS = ("dat-to-parquet", "parquet-to-dat")


def _env_note(env_name: str) -> str:
    return f"Configured from environment variable {env_name}"


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


def _prepare_dat_parquet(form: FormData) -> str:
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
    form["dst"] = _join_host_path(dst_root, root_subpath)
    form["dataset_profile"] = profile_name
    form["root_subpath"] = root_subpath
    return profile["source_note"]


# ─────────────────────────────────────────────────────────────────────────────
# Hooks
# ─────────────────────────────────────────────────────────────────────────────

_PAGE_CONTEXT: dict[str, Callable[[], Awaitable[dict[str, object]]]] = {
    "tec-suite": _tec_suite_page,
    "abstec-suite": _abstec_page,
    "dat-parquet-handler": _dat_parquet_page,
}

_PREPARE_FORM: dict[str, Callable[[FormData], str]] = {
    "tec-suite": _prepare_tec_suite,
    "abstec-suite": _prepare_abstec,
    "dat-parquet-handler": _prepare_dat_parquet,
}


def _default_page_context() -> dict[str, object]:
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


async def page_context(converter_name: str) -> dict[str, object]:
    """Template variables for a converter's run page."""
    context = _default_page_context()
    hook = _PAGE_CONTEXT.get(converter_name)
    if hook is not None:
        context.update(await hook())
    return context


def prepare_form(converter_name: str, form: FormData) -> str:
    """
    Validate a run form and fill in server-side paths, in place.

    Returns the note stored as the job's input path. Raises FormError when the
    form or the server configuration does not allow the run.
    """
    hook = _PREPARE_FORM.get(converter_name)
    if hook is None:
        return str(form.get("root", ""))
    return hook(form)
