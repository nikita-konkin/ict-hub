"""Observed 2017 AbsolTEC input screening and phase-leveling behavior.

Recovered from main 0x402cd0, especially 0x405af3..0x408555, using REA
6.9.0/Ghidra 12.2 and corroborated x86 instructions. This compatibility
path intentionally preserves zero placeholders, 30-second selection,
legacy phase repairs, and the shifted TEC/geometry association. These
rules are not recommendations for a new scientific method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .geometry import pierce_point

if TYPE_CHECKING:
    from .io import SatelliteData
    from .solver import PreparedObservations, SolverConfig


def _legacy_arrays(series: SatelliteData) -> tuple[np.ndarray, ...]:
    """The current-day formatted reader's six retained numeric columns."""
    # The original does not inspect the seventh, validity, column. It also
    # rejects a numeric row only when both phase and code are zero.
    seconds = np.asarray(series.hour) * 3600
    rounded = np.where(seconds < 0, np.ceil(seconds - 0.5), np.floor(seconds + 0.5))
    selected = ((series.phase_tec != 0) | (series.code_tec != 0)) & (rounded % 30 == 0)
    indices = np.flatnonzero(selected)[:1999]
    # Slots 0 and 2000 are zero sentinels in the original common blocks.
    # Two extra lookahead values are read by the spike classifier.
    arrays = [np.zeros(2003, dtype=float) for _ in range(6)]
    for target, source in zip(
        arrays[:5],
        (series.hour, series.elevation, series.azimuth, series.phase_tec, series.code_tec),
        strict=True,
    ):
        target[1 : len(indices) + 1] = source[indices]
    hour, elevation, azimuth, phase, code, delta = arrays
    delta[:] = code - phase
    return hour, elevation, azimuth, phase, code, delta, np.asarray([len(indices)])


def _arcs(hour: np.ndarray, elevation: np.ndarray, phase: np.ndarray) -> list[tuple[int, int]]:
    """Span above-ten-degree nonzero phase, permitting short interruptions."""
    # 0x405b09..0x405c7b: short invalid spans remain inside the same arc
    # when the next usable observation is within 124 seconds of its last
    # usable predecessor. TSN and the input validity mask are not consulted.
    valid = np.flatnonzero((elevation > 10) & (phase != 0))
    if not len(valid):
        return []
    splits = np.flatnonzero(np.abs(np.diff(hour[valid])) * 3600 > 124) + 1
    return [(int(indices[0]), int(indices[-1])) for indices in np.split(valid, splits)]


def _repair_arcs(
    arcs: list[tuple[int, int]], phase: np.ndarray, code: np.ndarray, delta: np.ndarray
) -> tuple[list[dict[int, int]], dict[str, int]]:
    """Translate scalar decisions, retaining the original recurrence order.

    A returned geometry map associates compact geometry index j with its
    original input index. The first stored index is two, not one.
    """
    geometry_maps: list[dict[int, int]] = []
    counts = {"legacy_code_spikes": 0, "legacy_phase_spikes": 0, "legacy_phase_shifts": 0}
    for start, end in arcs:
        mean = variance = 0.0
        previous_delta = previous_phase = previous_code = 0.0
        geometry: dict[int, int] = {}
        j = 2
        for current in range(start, end + 1):
            if code[current] == 0 and phase[current] == 0:
                phase[current] = 0
                continue
            before_mean, before_variance = mean, variance
            difference = delta[current] - before_mean
            # This variance recurrence includes the first raw offset squared,
            # matching 0x407d16..0x407db1 rather than a conventional variance.
            mean = difference / (j - 1) + before_mean
            variance = (difference * difference - before_variance) / (j - 1) + before_variance
            current_delta = delta[current]
            next_delta = delta[current + 1]
            geometry[j] = current + 1
            if current > start:
                threshold = np.sqrt(before_variance) * 4 if before_variance >= 0 else np.nan
                if not abs(current_delta - mean) <= threshold:
                    if abs(next_delta - mean) <= threshold or abs(delta[current + 2] - mean) <= threshold:
                        # Isolated code outlier. Its running-statistics update
                        # is undone and its code value is replaced by the last.
                        mean, variance = before_mean, before_variance
                        code[current] = previous_code
                        current_delta = (next_delta + previous_delta) * 0.5
                        delta[current] = current_delta
                        counts["legacy_code_spikes"] += 1
                    else:
                        # Sustained difference offset: shift all subsequent
                        # nonzero phase values, and the precomputed deltas.
                        tail = np.arange(current, end + 1)
                        tail = tail[phase[tail] != 0]
                        phase[tail] += previous_phase - phase[current]
                        delta[tail] += previous_delta - current_delta
                        current_delta = delta[current]
                        counts["legacy_phase_shifts"] += 1
                if phase[current] != 0:
                    phase_jump = previous_phase - phase[current]
                    if abs(phase_jump) > 5:
                        if (
                            abs(phase[current + 1] - previous_phase) <= 5
                            or abs(phase[current + 2] - previous_phase) <= 5
                        ):
                            phase[current] = previous_phase
                            counts["legacy_phase_spikes"] += 1
                        else:
                            tail = np.arange(current, end + 1)
                            tail = tail[phase[tail] != 0]
                            phase[tail] += phase_jump
                            # The separate five-TECU branch does not update
                            # the difference array in the original.
                            counts["legacy_phase_shifts"] += 1
            previous_delta = current_delta
            previous_code = code[current]
            previous_phase = phase[current]
            j += 1
        geometry_maps.append(geometry)
    return geometry_maps, counts


def prepare_legacy(satellites: list[SatelliteData], config: SolverConfig) -> PreparedObservations:
    """Prepare current-day GPS/GLONASS observations like the 2017 binary.

    Neighboring-day loading is left to the caller; this implementation's
    compatibility claim is restricted to supplied current-day files.
    Scientific parity is established separately by reference comparisons.
    """
    # Imported at call time: solver imports this route only when requested.
    from .solver import PreparedObservations, SolverError

    if not satellites:
        raise SolverError("No satellite observations supplied")
    total = sum(len(s.hour) for s in satellites)
    if total > config.max_observations:
        raise SolverError(f"{total} observations exceed the {config.max_observations} observation budget")
    supported = [s for s in satellites if s.satellite_id.startswith(("G", "R"))]
    if not supported:
        raise SolverError("No GPS or GLONASS observations supplied")
    if len({s.satellite_id for s in supported}) > config.max_satellites:
        raise SolverError(f"Input exceeds the {config.max_satellites}-satellite resource budget")
    if len({s.station for s in supported}) != 1:
        raise SolverError("A daily fit must contain exactly one station")
    if len({s.satellite_id for s in supported}) != len(supported):
        raise SolverError("Duplicate satellite files: merge overlapping epochs explicitly before fitting")
    if any(s.latitude_deg is None or s.longitude_deg is None for s in supported):
        raise SolverError("Station longitude/latitude headers are required")
    lat0, lon0 = supported[0].latitude_deg, supported[0].longitude_deg
    for series in supported:
        if (
            abs(float(series.latitude_deg) - float(lat0)) > 1e-4
            or abs((float(series.longitude_deg) - float(lon0) + 180) % 360 - 180) > 1e-4
        ):
            raise SolverError("Conflicting station coordinates across input files")
    n = int(np.ceil(24 / config.time_step_hours - 1e-10))
    epochs = np.arange(n + 1, dtype=float) * config.time_step_hours
    epochs = epochs[epochs <= 24 + 1e-9]
    fields: list[list[np.ndarray]] = [[] for _ in range(5)]
    masks = []
    ids: list[str] = []
    sv_indices = []
    counts = {
        "legacy_code_spikes": 0,
        "legacy_phase_spikes": 0,
        "legacy_phase_shifts": 0,
        "legacy_retained_input_observations": 0,
        "legacy_cap_truncated_files": 0,
        "accepted_arcs": 0,
        "rejected_arcs": 0,
    }
    for series in sorted(supported, key=lambda item: item.satellite_id):
        hour, elevation, azimuth, phase, code, delta, retained = _legacy_arrays(series)
        counts["legacy_retained_input_observations"] += int(retained[0])
        counts["legacy_cap_truncated_files"] += int(retained[0] == 1999 and len(series.hour) > 1999)
        arcs = _arcs(hour, elevation, phase)
        geometry_maps, repairs = _repair_arcs(arcs, phase, code, delta)
        for name, value in repairs.items():
            counts[name] += value
        eligible = np.zeros(len(epochs), dtype=bool)
        for start, end in arcs:
            raw = np.arange(start, end + 1)
            good = raw[elevation[raw] > config.elevation_cutoff]
            for k, center in enumerate(epochs):
                # The preliminary station eligibility check includes exact
                # boundaries; the equation accumulation later excludes them.
                if (
                    np.count_nonzero(
                        (hour[good] >= center - config.window_hours) & (hour[good] <= center + config.window_hours)
                    )
                    >= config.min_window_samples
                ):
                    eligible[k] = True
        if np.count_nonzero(eligible) <= 1:
            counts["rejected_arcs"] += len(arcs)
            continue
        chunks: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for (start, end), geometry in zip(arcs, geometry_maps, strict=True):
            if end - start < config.min_arc_samples:
                counts["rejected_arcs"] += 1
                continue
            raw = np.arange(start, end + 1)
            good = raw[(phase[raw] != 0) & (elevation[raw] > 10)]
            if not len(good):
                counts["rejected_arcs"] += 1
                continue
            # Preserve arithmetic summation order from 0x408447..0x40849b.
            difference_sum = 0.0
            for index in good:
                chosen_code = code[index] if np.any(code[index : index + 3] != 0) else 0.0
                difference_sum = (difference_sum + chosen_code) - phase[index]
            offset = difference_sum / len(good)
            shifted = np.arange(start + 2, end + 1)
            shifted = shifted[(phase[shifted] != 0) & (elevation[shifted] > 10)]
            compact_indices = np.arange(1, len(shifted) + 1)
            selected = np.asarray(
                [j in geometry and elevation[start + j - 1] > config.elevation_cutoff for j in compact_indices]
            )
            compact_indices, shifted = compact_indices[selected], shifted[selected]
            if not len(shifted):
                counts["rejected_arcs"] += 1
                continue
            # Geometry is calculated from current+1 while phase/code repairs
            # operate on current; the separately compacted leveled values
            # begin at arc_start+2. This observed misalignment is retained.
            geom_indices = np.asarray([geometry[int(j)] for j in compact_indices], dtype=int)
            time_indices = start + compact_indices - 1
            chunks.append((time_indices, geom_indices, phase[shifted] + offset))
            counts["accepted_arcs"] += 1
        if not chunks:
            continue
        ids.append(series.satellite_id)
        masks.append(eligible)
        station_lon = float(series.longitude_deg)
        if station_lon > 180:
            station_lon -= 360
        for time_indices, geom_indices, slant in chunks:
            lat, lon = pierce_point(
                elevation[geom_indices],
                azimuth[geom_indices],
                float(series.latitude_deg),
                station_lon,
                legacy_longitude=True,
            )
            sine = (
                6371302.0
                / (6371302.0 + 450.0)
                * np.sin(np.deg2rad(config.correction_coefficient * (90 - elevation[geom_indices])))
            )
            mapping = 1 / np.sqrt(1 - sine**2)
            for target, value in zip(
                fields,
                (hour[time_indices], slant, mapping, lon - station_lon, lat - float(series.latitude_deg)),
                strict=True,
            ):
                target.append(value)
            sv_indices.append(np.full(len(slant), len(ids) - 1, dtype=np.int64))
    if not ids:
        raise SolverError("No legacy-compatible arcs with at least two supported output windows")
    data = [np.concatenate(values) for values in fields]
    order = np.argsort(data[0], kind="stable")
    diagnostics = {
        **counts,
        "input_files": len(satellites),
        "unsupported_files": len(satellites) - len(supported),
        "input_observations": total,
        "leveled_observations": len(order),
        "satellites": len(ids),
        "preprocessing": "legacy-2017 recovered scalar repairs and shifted sample association",
        "legacy_context": "supplied current-day files only",
        "legacy_validity_flags_ignored": True,
    }
    return PreparedObservations(
        *(values[order] for values in data),
        np.concatenate(sv_indices)[order],
        tuple(ids),
        diagnostics,
        window_satellite_mask=np.column_stack(masks),
    )
