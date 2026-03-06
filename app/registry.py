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
            if value not in (None, "", flag.get("default")):
                cmd.extend([flag["name"], str(int(value))])
            elif flag.get("default") is not None:
                cmd.extend([flag["name"], str(int(flag["default"]))])

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
