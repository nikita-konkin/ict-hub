"""Report combined fitted biases using a per-constellation zero-mean gauge."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path


def split_biases(combined_bias: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """Return satellite and receiver biases in TECU, preserving every link sum.

    Satellite biases are constrained to zero mean only AFTER fitting. This
    convention does not add an artificial constraint to the TEC solution.
    The receiver term is one value per GPS/GLONASS constellation, not a
    calibrated hardware measurement or an external satellite DCB catalogue.
    """
    groups: dict[str, list[float]] = defaultdict(list)
    if not combined_bias:
        raise ValueError("No fitted biases to report")
    for name, value in combined_bias.items():
        if not re.fullmatch(r"[GR]\d{2,3}", name) or not math.isfinite(value):
            raise ValueError("Biases must have GPS/GLONASS satellite IDs and finite values")
        groups[name[0]].append(value)
    receivers = {name: math.fsum(values) / len(values) for name, values in groups.items()}
    satellites = {name: value - receivers[name[0]] for name, value in combined_bias.items()}
    return satellites, receivers


def write_dcb_output(path: str | Path, combined_bias: dict[str, float]) -> None:
    """Write legacy-shaped DCB text into an unpublished station staging folder.

    Refuses an existing file. The caller publishes the whole station folder
    transactionally together with TEC output and provenance diagnostics.
    """
    satellites, receivers = split_biases(combined_bias)
    with Path(path).open("x", encoding="ascii", newline="\n") as stream:
        stream.write("# DCB sat:\n")
        for name, value in sorted(satellites.items()):
            stream.write(f"{name[0]}{int(name[1:]):2d} {value:11.6f}\n")
        stream.write("# DCB rec:\n")
        for name, value in sorted(receivers.items()):
            stream.write(f"{name} {value:11.6f}\n")
