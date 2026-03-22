"""
registry.py — Converter registry.

This is the single source of truth for every converter the web UI knows about.
Adding a new converter means adding one entry to CONVERTERS and everything else
(the form, the command builder, the Docker invocation) adapts automatically.

Each entry describes:
  image       — Docker image name to run
  description — shown in the UI
  container_volumes — fixed container-side mount points
  flags       — list of flag descriptors that drive the form renderer

Flag descriptor fields:
  name     — CLI flag (e.g. "-j")
  long     — long form (e.g. "--jobs"), used in command building
  label    — human-readable label shown in the form
  type     — "checkbox" | "number" | "text" | "select"
  default  — default value
  required — whether the form validates this field (optional, default False)
  options  — list of (value, label) pairs for "select" type
  help     — tooltip / description text
  min/max  — for "number" type
"""
from __future__ import annotations
import shlex
from typing import Any

from app import config as cfg

import logging

logger = logging.getLogger(__name__)


CONVERTERS: dict[str, dict] = {
    "tec-suite": {
        "image": cfg.TECSUITE_IMAGE,
        "label": "TEC-Suite",
        "description": (
            "Reconstructs slant Total Electron Content (TEC) from GNSS RINEX observation "
            "data. Supports GPS, GLONASS, Galileo, BeiDou, GEO, and IRNSS systems."
        ),
        # Fixed container-side paths. Host-side paths are entered by the user in the form.
        "container_volumes": {
            "rinex": "/data/rinex",
            "output": "/app/out",
        },
        # Path used inside the container for the tecs configuration file.
        # This file must already exist in the container image.
        "cfg_path": "/app/tecs.cfg",
        "tecs_path": "/app/tecs.py",
        # Regex patterns used to extract a 0–100 progress value from log lines.
        # Patterns are tried in order; first match wins.
        "progress_patterns": [
            r"===\s*processing\s+day\s+folder:\s*([^\s]+)\s*===", # === processing day folder: /data/rinex/001 ===
            r"Completed.*?(\d+)\s*/\s*(\d+):\s+([\w.]+)", # "Completed file 5/20"
        ],
        "flags": [
            {
                "name": "-r",
                "long": "--root",
                "label": "RINEX Root Directory (host path)",
                "type": "text",
                "default": "",
                "required": True,
                "help": (
                    "Host path to the directory containing day-numbered sub-folders "
                    "with .zip RINEX archives. E.g. N:\\RINEX or /mnt/data/rinex"
                ),
                "is_volume": "rinex",  # special marker: this flag maps to a volume
            },
            {
                "name": "-o",
                "long": "--out",
                "label": "Output Directory (host path)",
                "type": "text",
                "default": "",
                "required": True,
                "help": (
                    "Host path for output files. E.g. N:\\tec-suite\\out "
                    "or /mnt/data/out"
                ),
                "is_volume": "output",  # maps to the output volume
            },
            {
                "name": "-j",
                "long": "--jobs",
                "label": "Parallel Jobs",
                "type": "number",
                "default": 4,
                "required": False,
                "help": "Number of day folders to process in parallel (default 4).",
                "min": 1,
                "max": 64,
            },
            {
                "name": "-v",
                "long": "--verbose",
                "label": "Verbose Output",
                "type": "checkbox",
                "default": False,
                "required": False,
                "help": "Print detailed processing information to the log.",
            },
            {
                "name": "-k",
                "long": "--cleanup",
                "label": "Cleanup Extracted Folders",
                "type": "checkbox",
                "default": True,
                "required": False,
                "help": "Delete extracted RINEX directories after processing.",
            },
        ],
    },
    "dat-parquet-handler": {
        "image": cfg.DAT_PARQUET_IMAGE,
        "label": "DAT <-> Parquet",
        "description": (
            "Converts tec-suite DAT files to Parquet format (or back), "
            "preserving the source directory layout."
        ),
        "log_emit_interval_sec": 1.0,
        "progress_patterns": [
            # Example: "INFO: Completed 2618 / 15221"
            r"Completed\s+(\d+)\s*/\s*(\d+)",
            # Example: "INFO: Progress: 17%"
            r"Progress:\s*(\d{1,3})\s*%",
        ],
        "container_volumes": {
            "src": "/data/src",
            "dst": "/data/dst",
        },
        "flags": [
            {
                "name": "--direction",
                "long": "--direction",
                "label": "Direction",
                "type": "select",
                "default": "dat-to-parquet",
                "required": True,
                "options": [
                    ("dat-to-parquet", "DAT -> Parquet"),
                    ("parquet-to-dat", "Parquet -> DAT"),
                ],
                "help": "Conversion direction.",
            },
            {
                "name": "-s",
                "long": "--src",
                "label": "Source Directory (host path)",
                "type": "text",
                "default": "",
                "required": True,
                "is_volume": "src",
                "help": "Host path to the source root directory.",
            },
            {
                "name": "-d",
                "long": "--dst",
                "label": "Destination Directory (host path)",
                "type": "text",
                "default": "",
                "required": False,
                "is_volume": "dst",
                "help": "Host path for output. Defaults to the same as source if left blank.",
            },
            {
                "name": "--overwrite",
                "long": "--overwrite",
                "label": "Overwrite Existing Files",
                "type": "checkbox",
                "default": False,
                "required": False,
                "help": "Overwrite destination files if they already exist.",
            },
        ],
    },
    "abstec-suite": {
        "image": cfg.ABSTEC_SUITE_IMAGE,
        "label": "AbsTEC Suite",
        "description": (
            "Runs TayAbsTEC from tec-suite DAT inputs, updates absolTEC.dia, and "
            "supports both single-run and multi-day batch execution."
        ),
        "log_emit_interval_sec": 1.0,
        "progress_patterns": [
            # Example: "Processing year=2026 day=001 site=cher001s08 (23/870)"
            r"Processing\s+year=\d{4}\s+day=\d{1,3}\s+site=[^\s]+\s*\((\d+)\s*/\s*(\d+)\)",
            # Example: "INFO: Completed 2618 / 15221"
            r"Organized station output under day folder:"
            r"Completed\s+(\d+)\s*/\s*(\d+)",
            # Example: "INFO: Progress: 17%"
            r"Progress:\s*(\d{1,3})\s*%",
        ],
        "container_volumes": {
            "dat_path": "/data/in",
            "output_dir": "/data/out",
        },
        "flags": [
            {
                "name": "--dat-path",
                "long": "--dat-path",
                "label": "Input DAT Root (host path)",
                "type": "text",
                "default": "",
                "required": True,
                "is_volume": "dat_path",
                "help": "Host path mounted to /data/in containing in/YYYY/DDD/SITE/*.dat.",
            },
            {
                "name": "--output-dir",
                "long": "--output-dir",
                "label": "Output Root (host path)",
                "type": "text",
                "default": "",
                "required": False,
                "is_volume": "output_dir",
                "help": "Optional host output path mounted to /data/out.",
            },
            {
                "name": "--workdir",
                "long": "--workdir",
                "label": "Workdir (container path)",
                "type": "text",
                "default": "/data/workdir",
                "required": False,
                "help": "Path to TayAbsTEC binaries inside the container.",
            },
            {
                "name": "--year",
                "long": "--year",
                "label": "Year",
                "type": "number",
                "default": "",
                "required": True,
                "help": "4-digit year.",
                "min": 2000,
                "max": 2100,
            },
            {
                "name": "--day-of-year",
                "long": "--day-of-year",
                "label": "Day Of Year (single run)",
                "type": "number",
                "default": "",
                "required": False,
                "help": "Single day number (1-366). Mutually exclusive with --days.",
                "min": 1,
                "max": 366,
            },
            {
                "name": "--days",
                "long": "--days",
                "label": "Days (batch mode)",
                "type": "text",
                "default": "",
                "required": False,
                "help": "Comma list or ranges, e.g. 001,002,003 or 001-365.",
            },
            {
                "name": "--site",
                "long": "--site",
                "label": "Site",
                "type": "text",
                "default": "",
                "required": False,
                "help": "Station/site name for single-run mode (e.g. aksu0010).",
            },
            {
                "name": "--elevation-cutoff",
                "long": "--elevation-cutoff",
                "label": "Elevation Cutoff",
                "type": "number",
                "default": 10,
                "required": False,
                "help": "Elevation cutoff in degrees.",
                "min": 0,
                "max": 90,
            },
            {
                "name": "--time-step-hours",
                "long": "--time-step-hours",
                "label": "Time Step Hours",
                "type": "text",
                "default": "0.5",
                "required": False,
                "help": "DIA time step in hours (supports decimals, e.g. 0.5).",
            },
            {
                "name": "--correction-coefficient",
                "long": "--correction-coefficient",
                "label": "Correction Coefficient",
                "type": "text",
                "default": "0.97",
                "required": False,
                "help": "Bias/correction coefficient used for processing.",
            },
            {
                "name": "--runner",
                "long": "--runner",
                "label": "Runner",
                "type": "select",
                "default": "auto",
                "required": False,
                "options": [
                    ("auto", "auto"),
                    ("wine", "wine"),
                    ("direct", "direct"),
                ],
                "help": "Execution backend for absolTEC.exe.",
            },
            {
                "name": "--execution-timeout-seconds",
                "long": "--execution-timeout-seconds",
                "label": "Execution Timeout (seconds)",
                "type": "number",
                "default": "",
                "required": False,
                "help": "Optional timeout to avoid hung runs.",
                "min": 1,
            },
            {
                "name": "--dry-run",
                "long": "--dry-run",
                "label": "Dry Run",
                "type": "checkbox",
                "default": True,
                "required": False,
                "help": "Validate and update absolTEC.dia without launching absolTEC.exe.",
            },
        ],
    },
    # ── Add future converters here ────────────────────────────────────────────
    # "my_converter": {
    #     "image": "my-converter:latest",
    #     "label": "My Converter",
    #     "flags": [...],
    # },
}


def get_converter(name: str) -> dict | None:
    """Return the converter config dict or None if not registered."""
    return CONVERTERS.get(name)


def build_command(converter_name: str, form_data: dict[str, Any]) -> tuple[list[str], dict[str, dict]]:
    """
    Build the Docker command list and volume mapping from user form data.

    Returns (command, volumes) where:
      - command is a list of strings passed as the container's command
      - volumes is a dict in Docker SDK format:
        { "host_path": {"bind": "container_path", "mode": "rw"} }

    Volume-type flags (is_volume) are translated into Docker volume mounts
    rather than CLI arguments, because their value is the host path but the
    container always sees a fixed path.
    """
    conv = CONVERTERS[converter_name]
    cmd: list[str] = []
    volumes: dict[str, dict] = {}

    for flag in conv["flags"]:
        key = flag["long"].lstrip("-").replace("-", "_")  # "--output-dir" → "output_dir"
        value = form_data.get(key, flag.get("default"))

        if flag.get("is_volume"):
            # Map the host path (user input) → fixed container path (from registry)
            host_path = str(value).strip()
            if not host_path:
                continue
            container_path = conv["container_volumes"][flag["is_volume"]]
            # RINEX must be writable because TEC-Suite extracts archives under
            # /data/rinex/<day>/... before processing.
            mode = "rw"
            volumes[host_path] = {"bind": container_path, "mode": mode}
            # Also emit the CLI flag pointing at the container-side path
            cmd.extend([flag["name"], container_path])

        elif flag["type"] == "checkbox":
            # Checkbox: emit the flag only when checked
            if value in (True, "on", "true", "1", True):
                cmd.append(flag["name"])

        elif flag["type"] == "number":
            # Prefer user input; otherwise fall back to default. Skip empty values.
            number_value = value if value not in (None, "") else flag.get("default")
            if number_value not in (None, ""):
                cmd.extend([flag["name"], str(int(number_value))])

        elif flag["type"] in ("text", "select"):
            if value:
                cmd.extend([flag["name"], str(value)])

    # Always wire in the fixed config and tecs script paths
    if "cfg_path" in conv:
        cmd.extend(["-c", conv["cfg_path"]])
    if "tecs_path" in conv:
        cmd.extend(["-t", conv["tecs_path"]])

    logger.debug(f"Built command for converter '{converter_name}': {cmd} with volumes {volumes}")

    return cmd, volumes
