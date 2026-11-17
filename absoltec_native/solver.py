"""A bounded-memory reconstruction of the seven-coefficient daily TEC fit.

Model/window/weight evidence: REA 6.9.0 + Ghidra 12.2, original SHA256
31984eb0df935b52751cf92458b4471ca8260a98fed9d5fb6492fe175d3443f5,
main at 0x402cd0 (window/weight at 0x405eae..0x405f28).
Legacy screening is reconstructed in legacy.py. The published model uses
validity-aware screening instead. See docs/absoltec_reconstruction.md for
the remaining differences and validation limits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy import linalg

from . import __version__
from .geometry import longitude_offset, mapping_factor, pierce_point, turbotec_alpha
from .io import SatelliteData


class SolverError(ValueError):
    """The observations cannot support a reliable daily TEC solution."""


@dataclass(frozen=True)
class SolverConfig:
    time_step_hours: float = 0.5
    elevation_cutoff: float = 10.0
    correction_coefficient: float = 0.97
    model: Literal["legacy-2017", "published"] = "legacy-2017"
    window_hours: float = 1.0
    min_arc_samples: int = 21
    min_window_samples: int = 20
    min_satellites: int = 6
    max_output_epochs: int = 8640
    max_observations: int = 2_000_000
    max_satellites: int = 128
    condition_limit: float = 1e10
    auto_mapping_alpha: bool = False
    mapping_shell_height_km: float = 450.0
    nonneg: bool = False

    def __post_init__(self) -> None:
        if not np.isfinite(self.time_step_hours) or not 0 < self.time_step_hours <= 24:
            raise ValueError("Time step must be finite and in (0, 24] hours")
        if np.ceil(24 / self.time_step_hours) > self.max_output_epochs:
            raise ValueError(f"Time step exceeds the {self.max_output_epochs}-epoch resource budget")
        if not np.isfinite(self.elevation_cutoff) or not 0 <= self.elevation_cutoff < 90:
            raise ValueError("Elevation cutoff must be finite and in [0, 90) degrees")
        if not np.isfinite(self.correction_coefficient) or not 0 < self.correction_coefficient <= 1:
            raise ValueError("Correction coefficient must be finite and in (0, 1]")
        if self.model not in {"legacy-2017", "published"}:
            raise ValueError("Model must be legacy-2017 or published")
        if not np.isfinite(self.window_hours) or not 0 < self.window_hours <= 6:
            raise ValueError("Window half-width must be finite and in (0, 6] hours")
        if not np.isfinite(self.mapping_shell_height_km) or not 0 < self.mapping_shell_height_km <= 2000:
            raise ValueError("Mapping shell height must be finite and in (0, 2000] km")
        for name in ("auto_mapping_alpha", "nonneg"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        for name in (
            "min_arc_samples",
            "min_window_samples",
            "min_satellites",
            "max_output_epochs",
            "max_observations",
            "max_satellites",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not np.isfinite(self.condition_limit) or self.condition_limit <= 1:
            raise ValueError("Condition limit must be finite and greater than one")
        if self.model == "legacy-2017":
            # Parity with the recovered 2017 binary: its literals and its
            # unconstrained solve are part of what is being reproduced.
            if self.auto_mapping_alpha:
                raise ValueError("auto_mapping_alpha requires model='published'; the legacy binary fixes alpha")
            if self.mapping_shell_height_km != 450.0:
                raise ValueError(
                    "mapping_shell_height_km cannot differ from 450 with model='legacy-2017'; the legacy binary hardcodes it"
                )
            if self.nonneg:
                raise ValueError("nonneg requires model='published'; the legacy binary solves unconstrained")


@dataclass(frozen=True)
class PreparedObservations:
    hour: NDArray[np.float64]
    slant_tec: NDArray[np.float64]
    mapping: NDArray[np.float64]
    dlon: NDArray[np.float64]
    dlat: NDArray[np.float64]
    satellite: NDArray[np.int64]
    satellite_ids: tuple[str, ...]
    diagnostics: dict
    window_satellite_mask: NDArray[np.bool_] | None = None


@dataclass(frozen=True)
class Solution:
    rows: NDArray[np.float64]
    combined_bias: dict[str, float]
    diagnostics: dict


def prepare_observations(satellites: list[SatelliteData], config: SolverConfig) -> PreparedObservations:
    """Select recovered legacy screening or independent validity-aware screening."""
    if config.model == "legacy-2017":
        from .legacy import prepare_legacy

        return prepare_legacy(satellites, config)
    return _prepare_modern(satellites, config)


def _prepare_modern(satellites: list[SatelliteData], config: SolverConfig) -> PreparedObservations:
    """Level independent phase arcs without using a reference TEC series.

    Missing code does not split an otherwise continuous phase arc. Explicit
    slips, missing phase and gaps do. We neither interpolate code spikes nor
    edit all subsequent samples to hide a phase jump, as the old binary does.

    Leveling follows TuRBOTEC stage 4 (Yasyukevich et al. 2020, Eq. for
    ``const``): the arc offset is the 1/S elevation-weighted mean of
    code-minus-phase differences. The printed paper formula contains a stray
    factor; the dimensionally consistent weighted mean is implemented here.
    """
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
    stations = {s.station for s in supported}
    if len(stations) != 1:
        raise SolverError("A daily fit must contain exactly one station")
    coordinates = [(s.latitude_deg, s.longitude_deg) for s in supported]
    if any(lat is None or lon is None for lat, lon in coordinates):
        raise SolverError("Station longitude/latitude headers are required")
    lat0, lon0 = coordinates[0]
    if lat0 is None or lon0 is None:
        raise SolverError("Station longitude/latitude headers are required")
    for lat, lon in coordinates:
        if lat is None or lon is None:
            raise SolverError("Station longitude/latitude headers are required")
        if abs(lat - lat0) > 1e-4 or abs(float(longitude_offset(lon, lon0))) > 1e-4:
            raise SolverError("Conflicting station coordinates across input files")
    correction = config.correction_coefficient
    if config.auto_mapping_alpha:
        correction = turbotec_alpha(float(lat0))
    chunks: list[tuple[str, NDArray, NDArray]] = []
    arcs_accepted = arcs_rejected = missing_phase = missing_code = 0
    for series in supported:
        n = len(series.hour)
        phase_ok = series.phase_valid & np.isfinite(series.phase_tec)
        code_ok = series.code_valid & np.isfinite(series.code_tec)
        missing_phase += int(np.count_nonzero(~phase_ok))
        missing_code += int(np.count_nonzero(~code_ok))
        # Arc screening at 10 degrees is present in the original before the
        # configurable fitting cutoff. Higher cutoffs do not change leveling.
        usable = phase_ok & (series.elevation > 10)
        breaks = series.break_before.copy()
        if n:
            breaks[0] = True
            breaks[1:] |= ~usable[:-1] | ~usable[1:]
        boundaries = np.r_[0, np.flatnonzero(breaks[1:]) + 1, n]
        for start, stop in pairwise(boundaries):
            indices = np.arange(start, stop)
            indices = indices[usable[indices]]
            lev = indices[code_ok[indices]]
            if len(indices) < config.min_arc_samples or len(lev) < 2:
                arcs_rejected += 1
                continue
            # TuRBOTEC stage 4: 1/S elevation-weighted leveling offsets.
            weights = 1.0 / mapping_factor(
                series.elevation[lev],
                correction=correction,
                shell_height_km=config.mapping_shell_height_km,
                earth_radius=6371.0,
            )
            offset = float(np.sum((series.code_tec[lev] - series.phase_tec[lev]) * weights) / np.sum(weights))
            indices = indices[series.elevation[indices] > config.elevation_cutoff]
            if not len(indices):
                arcs_rejected += 1
                continue
            arcs_accepted += 1
            chunks.append((series.satellite_id, indices, np.full(len(indices), offset)))
    if not chunks:
        raise SolverError("No continuous phase arcs with enough valid code observations")
    return _assemble_observations(
        supported,
        chunks,
        config,
        {
            "input_files": len(satellites),
            "unsupported_files": len(satellites) - len(supported),
            "input_observations": total,
            "accepted_arcs": arcs_accepted,
            "rejected_arcs": arcs_rejected,
            "missing_phase": missing_phase,
            "missing_code": missing_code,
            "leveling": "1/S elevation-weighted phase-to-code mean (TuRBOTEC stage 4)",
            "mapping_correction": correction,
            "mapping_shell_height_km": config.mapping_shell_height_km,
        },
    )


def _assemble_observations(satellites, chunks, config, diagnostics) -> PreparedObservations:
    # Input files for the same satellite are deliberately refused until an
    # explicit neighboring-day merge can preserve overlap and arc identities.
    by_id = {s.satellite_id: s for s in satellites}
    if len(by_id) != len(satellites):
        raise SolverError("Duplicate satellite files: merge overlapping epochs explicitly before fitting")
    ids = tuple(sorted({c[0] for c in chunks}))
    id_index = {name: i for i, name in enumerate(ids)}
    fields: list[list[NDArray]] = [[] for _ in range(5)]
    sv_indices = []
    correction = float(diagnostics["mapping_correction"])
    shell_height = float(diagnostics["mapping_shell_height_km"])
    for name, indices, offsets in chunks:
        series = by_id[name]
        if series.latitude_deg is None or series.longitude_deg is None:
            raise SolverError("Station longitude/latitude headers are required")
        legacy = config.model == "legacy-2017"
        lat, lon = pierce_point(
            series.elevation[indices],
            series.azimuth[indices],
            series.latitude_deg,
            series.longitude_deg,
            legacy_longitude=legacy,
            shell_height_km=shell_height,
        )
        values = (
            series.hour[indices],
            series.phase_tec[indices] + offsets,
            mapping_factor(
                series.elevation[indices],
                correction=correction,
                earth_radius=6371302.0 if legacy else 6371.0,
                shell_height_km=shell_height,
            ),
            longitude_offset(lon, series.longitude_deg),
            lat - series.latitude_deg,
        )
        for target, value in zip(fields, values, strict=True):
            target.append(value)
        sv_indices.append(np.full(len(indices), id_index[name], dtype=np.int64))
    data = [np.concatenate(f) for f in fields]
    order = np.argsort(data[0], kind="stable")
    diagnostics["leveled_observations"] = len(order)
    diagnostics["satellites"] = len(ids)
    return PreparedObservations(*(x[order] for x in data), np.concatenate(sv_indices)[order], ids, diagnostics)


@dataclass
class _Window:
    epoch: int
    qtb: NDArray
    qtz: NDArray
    r: NDArray
    scales: NDArray
    count: int
    condition: float


def fit_prepared(observations: PreparedObservations, config: SolverConfig) -> Solution:
    """QR-eliminate local coefficients and solve only a satellite-sized system.

    A one-hot satellite design is accumulated with bincount, never as a
    day-sized dense matrix. Only seven-column QR workspaces are allocated for
    one window at a time. Global storage is O(K*7*N_sat + N_sat**2).

    With ``config.nonneg`` the consistent 24 h system is instead solved as a
    bounded-variable least-squares problem (TuRBOTEC Eq. 8); see _fit_bvls.
    """
    if config.nonneg:
        return _fit_bvls(observations, config)
    o = observations
    ns = len(o.satellite_ids)
    if ns > config.max_satellites or len(o.hour) > config.max_observations:
        raise SolverError("Prepared observations exceed the satellite or observation resource budget")
    if ns < config.min_satellites:
        raise SolverError(f"Only {ns} usable satellites; need at least {config.min_satellites}")
    # Include 24h as an internal fit window like the original, but exclude
    # it from the reported day. Multiplication avoids accumulated rounding.
    n = int(np.ceil(24 / config.time_step_hours - 1e-10))
    epochs = np.arange(n + 1, dtype=float) * config.time_step_hours
    epochs = epochs[epochs <= 24 + 1e-9]
    if o.window_satellite_mask is not None and o.window_satellite_mask.shape != (len(epochs), ns):
        raise SolverError("Legacy window eligibility shape does not match epochs and satellites")
    gram = np.zeros((ns, ns))
    rhs = np.zeros(ns)
    windows: list[_Window] = []
    missing: list[dict] = []
    max_window_rows = 0
    for k, t0 in enumerate(epochs):
        # Legacy code excludes exactly +/-1h rather than including edges.
        a = np.searchsorted(o.hour, t0 - config.window_hours, side="right")
        b = np.searchsorted(o.hour, t0 + config.window_hours, side="left")
        indices = np.arange(a, b)
        if o.window_satellite_mask is not None:
            indices = indices[o.window_satellite_mask[k, o.satellite[indices]]]
        m = len(indices)
        sv = o.satellite[indices]
        if m < config.min_window_samples or len(np.unique(sv)) < config.min_satellites:
            missing.append({"ut": float(t0), "reason": "insufficient observations or satellites", "observations": m})
            continue
        dt = t0 - o.hour[indices]
        if config.model == "published":
            dt = -dt
        dl, db, mapping = o.dlon[indices], o.dlat[indices], o.mapping[indices]
        # In slant space w=1/[S*(1+(dt/window)^2)]. Dividing the
        # observation by S gives polynomial-space weight S/(1+...).
        temporal_weight = 1 / (1 + (dt / config.window_hours) ** 2)
        sw = np.sqrt(mapping * temporal_weight)
        design = np.column_stack((np.ones(m), dl, db, dl**2, db**2, dt, dt**2)) * sw[:, None]
        scales = np.linalg.norm(design, axis=0)
        if np.any(scales == 0):
            missing.append({"ut": float(t0), "reason": "rank-deficient geometry", "observations": m})
            continue
        design /= scales
        q, r = linalg.qr(design, mode="economic", check_finite=False)
        singular = linalg.svdvals(r, check_finite=False)
        condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
        if condition > config.condition_limit:
            missing.append({"ut": float(t0), "reason": "ill-conditioned geometry", "observations": m})
            continue
        weighted_tec = o.slant_tec[indices] / mapping * sw
        bias_scale = sw / mapping
        qtb = q.T @ weighted_tec
        qtz = np.vstack([np.bincount(sv, weights=q[:, j] * bias_scale, minlength=ns) for j in range(7)])
        gram += np.diag(np.bincount(sv, weights=bias_scale**2, minlength=ns)) - qtz.T @ qtz
        rhs += np.bincount(sv, weights=bias_scale * weighted_tec, minlength=ns) - qtz.T @ qtb
        windows.append(_Window(k, qtb, qtz, r, scales, m, condition))
        max_window_rows = max(max_window_rows, m)
    if not windows:
        raise SolverError("No full-rank windows: insufficient or degenerate satellite geometry")
    # Scale the small Schur system. Eigenspectrum exposes unidentified biases;
    # neither damping nor a fabricated zero-mean bias hides rank loss.
    diagonal = np.diag(gram)
    if np.any(diagonal <= 0):
        raise SolverError("Satellite biases are not identifiable from these observation windows")
    bias_scales = np.sqrt(diagonal)
    normalized = gram / bias_scales[:, None] / bias_scales[None, :]
    normalized = (normalized + normalized.T) / 2
    values = linalg.eigvalsh(normalized, check_finite=False)
    if values[0] <= values[-1] / config.condition_limit:
        raise SolverError("Daily bias fit is rank-deficient or ill-conditioned; no output published")
    bias = linalg.solve(normalized, rhs / bias_scales, assume_a="pos", check_finite=False) / bias_scales
    coefficients = np.full((len(epochs), 7), np.nan)
    for win in windows:
        coefficients[win.epoch] = linalg.solve_triangular(win.r, win.qtb - win.qtz @ bias) / win.scales
    output = epochs < 24 - 1e-9
    rows = np.column_stack((epochs[output], coefficients[output]))
    diagnostics = {
        **o.diagnostics,
        "implementation_version": __version__,
        "config": asdict(config),
        "scientific_parity": "unverified",
        "output_epochs": len(rows),
        "solved_epochs": int(np.count_nonzero(np.isfinite(rows[:, 1]))),
        "missing_windows": missing,
        "internal_windows": len(windows),
        "max_window_observations": max_window_rows,
        "max_local_condition": max(w.condition for w in windows),
        "bias_system_condition": float(values[-1] / values[0]),
        "solver": "local column-scaled QR with satellite Schur elimination",
        "time_coefficient_convention": "backward (t0-t)" if config.model == "legacy-2017" else "forward (t-t0)",
        "mapping_radius_literal": 6371302.0 if config.model == "legacy-2017" else 6371.0,
    }
    return Solution(rows, dict(zip(o.satellite_ids, map(float, bias), strict=True)), diagnostics)


def _fit_bvls(o: PreparedObservations, config: SolverConfig) -> Solution:
    """Bounded-variable least squares on the consistent 24 h system.

    Reproduces TuRBOTEC Eq. 8 (Yasyukevich et al. 2020): every vertical TEC
    estimate must exceed C = 0.5 TECU, and every combined link bias must stay
    below the smallest experimental slant TEC of its satellite minus C, so
    slant TEC remains non-negative along every line of sight. The same
    screening, windows, weighting and column scaling as the unconstrained
    path are used; scipy.optimize.lsq_linear solves the bounded problem
    (Start-Parker 'bvls' when it fits the resource budget, sparse-capable
    'trf' otherwise).
    """
    from scipy import optimize, sparse

    ns = len(o.satellite_ids)
    if ns > config.max_satellites or len(o.hour) > config.max_observations:
        raise SolverError("Prepared observations exceed the satellite or observation resource budget")
    if ns < config.min_satellites:
        raise SolverError(f"Only {ns} usable satellites; need at least {config.min_satellites}")
    if o.window_satellite_mask is not None:
        raise SolverError("Bounded-variable fitting does not support the legacy eligibility mask")
    # Include 24h as an internal fit window like the original, but exclude
    # it from the reported day. Multiplication avoids accumulated rounding.
    n = int(np.ceil(24 / config.time_step_hours - 1e-10))
    epochs = np.arange(n + 1, dtype=float) * config.time_step_hours
    epochs = epochs[epochs <= 24 + 1e-9]
    screened: list[tuple[int, NDArray, NDArray, NDArray, NDArray, NDArray, NDArray, float]] = []
    missing: list[dict] = []
    max_window_rows = 0
    for k, t0 in enumerate(epochs):
        a = np.searchsorted(o.hour, t0 - config.window_hours, side="right")
        b = np.searchsorted(o.hour, t0 + config.window_hours, side="left")
        indices = np.arange(a, b)
        m = len(indices)
        sv = o.satellite[indices]
        if m < config.min_window_samples or len(np.unique(sv)) < config.min_satellites:
            missing.append({"ut": float(t0), "reason": "insufficient observations or satellites", "observations": m})
            continue
        dt = t0 - o.hour[indices]
        if config.model == "published":
            dt = -dt
        dl, db, mapping = o.dlon[indices], o.dlat[indices], o.mapping[indices]
        temporal_weight = 1 / (1 + (dt / config.window_hours) ** 2)
        sw = np.sqrt(mapping * temporal_weight)
        design = np.column_stack((np.ones(m), dl, db, dl**2, db**2, dt, dt**2)) * sw[:, None]
        scales = np.linalg.norm(design, axis=0)
        if np.any(scales == 0):
            missing.append({"ut": float(t0), "reason": "rank-deficient geometry", "observations": m})
            continue
        q, r = linalg.qr(design, mode="economic", check_finite=False)
        singular = linalg.svdvals(r, check_finite=False)
        condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
        if condition > config.condition_limit:
            missing.append({"ut": float(t0), "reason": "ill-conditioned geometry", "observations": m})
            continue
        weighted_tec = o.slant_tec[indices] / mapping * sw
        screened.append((k, indices, sv, dt, weighted_tec, design / scales, scales, condition))
        max_window_rows = max(max_window_rows, m)
    if not screened:
        raise SolverError("No full-rank windows: insufficient or degenerate satellite geometry")
    n_local = 7 * len(screened)
    bias_offset = n_local
    # Bias column norms, so every column of the bounded system is comparably
    # scaled, like the unconstrained path's column scaling. A bias column
    # carries sw/mapping per observation, not just one-hot ones.
    bias_norm = np.zeros(ns)
    for _, indices, sv, dt, _, _, _, _ in screened:
        mapping = o.mapping[indices]
        temporal_weight = 1 / (1 + (dt / config.window_hours) ** 2)
        sw = np.sqrt(mapping * temporal_weight)
        bias_scale = sw / mapping
        bias_norm += np.bincount(sv, weights=bias_scale**2, minlength=ns)
    if np.any(bias_norm <= 0):
        raise SolverError("Satellite biases are not identifiable from these observation windows")
    bias_scales = np.sqrt(bias_norm)
    min_exp = np.array([np.min(o.slant_tec[o.satellite == j]) for j in range(ns)])
    if not np.all(np.isfinite(min_exp)):
        raise SolverError("Prepared slant TEC contains non-finite values")
    rows: list[int] = []
    cols: list[int] = []
    values: list[float] = []
    rhs_parts: list[NDArray] = []
    row_offset = 0
    for pos, (_, indices, sv, dt, weighted_tec, scaled_design, _, _) in enumerate(screened):
        col_start = 7 * pos
        rhs_parts.append(weighted_tec)
        for column in range(7):
            rows.extend(np.arange(row_offset, row_offset + len(indices)))
            cols.extend([col_start + column] * len(indices))
            values.extend(scaled_design[:, column].tolist())
        mapping = o.mapping[indices]
        temporal_weight = 1 / (1 + (dt / config.window_hours) ** 2)
        sw = np.sqrt(mapping * temporal_weight)
        scaled_bias = (sw / mapping) / bias_scales[sv]
        rows.extend(np.arange(row_offset, row_offset + len(indices)))
        cols.extend((bias_offset + sv).tolist())
        values.extend(scaled_bias.tolist())
        row_offset += len(indices)
    matrix = sparse.csr_matrix((values, (rows, cols)), shape=(row_offset, n_local + ns))
    rhs = np.concatenate(rhs_parts)
    lb = np.full(n_local + ns, -np.inf)
    ub = np.full(n_local + ns, np.inf)
    for pos, (_, _, _, _, _, _, scales, _) in enumerate(screened):
        lb[7 * pos] = 0.5 * scales[0]  # I_V >= C = 0.5 TECU, scaled space
    ub[bias_offset:] = (min_exp - 0.5) * bias_scales  # I_DCB <= min(I_Exp) - C
    # The paper uses scipy's Start-Parker bvls routine, which requires a dense
    # matrix; the sparse-capable TRF algorithm solves the same bounded problem
    # when the dense conversion would exceed the resource budget.
    if row_offset * (n_local + ns) <= 5_000_000:
        system = matrix.toarray()
        method = "bvls"
    else:
        system = matrix
        method = "trf"
    result = optimize.lsq_linear(system, rhs, bounds=(lb, ub), method=method)
    x = result.x
    coefficients = np.full((len(epochs), 7), np.nan)
    for pos, (k, _, _, _, _, _, scales, _) in enumerate(screened):
        coefficients[k] = x[7 * pos : 7 * pos + 7] / scales
    bias = x[bias_offset:] / bias_scales
    active = int(np.count_nonzero((x <= lb + 1e-8) | (x >= ub - 1e-8)))
    output = epochs < 24 - 1e-9
    rows_out = np.column_stack((epochs[output], coefficients[output]))
    diagnostics = {
        **o.diagnostics,
        "implementation_version": __version__,
        "config": asdict(config),
        "scientific_parity": "unverified",
        "output_epochs": len(rows_out),
        "solved_epochs": int(np.count_nonzero(np.isfinite(rows_out[:, 1]))),
        "missing_windows": missing,
        "internal_windows": len(screened),
        "max_window_observations": max_window_rows,
        "max_local_condition": max(entry[7] for entry in screened),
        "solver": "bounded-variable least squares (TuRBOTEC Eq. 8, C=0.5 TECU)",
        "nonneg_solver": method,
        "active_bounds": active,
        "time_coefficient_convention": "forward (t-t0)",
        "mapping_radius_literal": 6371.0,
    }
    return Solution(rows_out, dict(zip(o.satellite_ids, map(float, bias), strict=True)), diagnostics)


def reconstruct(satellites: list[SatelliteData], config: SolverConfig | None = None) -> Solution:
    """Estimate one station-day; no legacy executable, VM, or reference required."""
    config = config or SolverConfig()
    return fit_prepared(prepare_observations(satellites, config), config)
