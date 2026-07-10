"""
tec_map_fields.py — Derived scalar fields for the TEC map pipeline (ict-hub).

Converts a VTEC grid [TECU] into transionospheric propagation characteristics:

  GDD  — group delay dispersion magnitude |D| = 3·K·N_t / (2·c·π·f³),
         reported in ns/GHz (1 ns/GHz == 1e-18 s/Hz exactly);
  B_k  — coherence bandwidth B_k = sqrt(c·f³ / (K·π·N_t)), reported in MHz.

Physical constants and band frequencies mirror
tec-stat/app/services/propagation.py — keep the two in sync.
"""

from __future__ import annotations

import numpy as np

LIGHT_SPEED = 3.0 * (10 ** 8)  # m/s
TEC_TO_NT = 10.0 ** 16         # TECU -> electrons/m^2
PROPAGATION_COEFF = 80.5

# Carrier frequencies in Hz (same table as tec-stat SIGNAL_BAND_FREQUENCIES_HZ).
# GLONASS L1/L2 are FDMA (f = f0 + k·Δf, k = −7…+6); the table uses the k=0
# centre frequencies. L3 is CDMA with a fixed carrier.
SIGNAL_BAND_FREQUENCIES_HZ: dict[str, float] = {
    "gps_l1": 1575.42e6,
    "gps_l2": 1227.60e6,
    "gps_l5": 1176.45e6,
    "glonass_l1": 1602.0e6,
    "glonass_l2": 1246.0e6,
    "glonass_l3": 1202.025e6,
    "galileo_e1": 1575.42e6,
    "galileo_e5a": 1176.45e6,
    "galileo_e5b": 1207.14e6,
    "galileo_e5": 1191.795e6,
    "bds_b1i": 1561.098e6,
    "bds_b1c": 1575.42e6,
    "bds_b2a": 1176.45e6,
    "bds_b2i": 1207.14e6,
}

SECONDS_PER_HZ_TO_NS_PER_GHZ = 1.0e18  # 1 s/Hz == 1e18 ns/GHz


def resolve_signal_band(name: str | None) -> tuple[str, float]:
    """Normalize a band name and return (canonical_name, frequency_hz)."""
    text = str(name or "gps_l1").strip().lower()
    if text not in SIGNAL_BAND_FREQUENCIES_HZ:
        supported = ", ".join(sorted(SIGNAL_BAND_FREQUENCIES_HZ))
        raise ValueError(f"Unsupported signal_band: {name!r}. Use one of: {supported}.")
    return text, SIGNAL_BAND_FREQUENCIES_HZ[text]


def signal_band_label(band: str) -> str:
    """Human-readable band label, e.g. 'gps_l1' -> 'GPS L1'."""
    system, _, signal = band.partition("_")
    system_names = {"gps": "GPS", "glonass": "GLONASS", "galileo": "Galileo", "bds": "BeiDou"}
    return f"{system_names.get(system, system.upper())} {signal.upper()}"


def compute_gdd_grid(vtec_grid: np.ndarray, frequency_hz: float) -> np.ndarray:
    """
    |D| (group delay dispersion magnitude) in ns/GHz from VTEC in TECU.

    Pointwise transform; NaN cells propagate. VTEC <= 0 yields 0 (a flat,
    dispersion-free channel), matching the tec-stat convention of ignoring
    non-physical non-positive TEC samples.
    """
    grid = np.asarray(vtec_grid, dtype=float)
    n_t = np.where(grid > 0.0, grid, 0.0) * TEC_TO_NT
    magnitude_s_per_hz = (3.0 * PROPAGATION_COEFF * n_t) / (
        2.0 * LIGHT_SPEED * np.pi * float(frequency_hz) ** 3
    )
    return np.where(np.isfinite(grid), magnitude_s_per_hz * SECONDS_PER_HZ_TO_NS_PER_GHZ, np.nan)


def compute_bk_grid(vtec_grid: np.ndarray, frequency_hz: float, min_tecu: float = 0.1) -> np.ndarray:
    """
    Coherence bandwidth B_k in MHz from VTEC in TECU.

    Cells with VTEC below `min_tecu` are masked to NaN: B_k diverges as
    TEC -> 0, and near-zero VTEC in the relative scale is noise anyway.
    """
    grid = np.asarray(vtec_grid, dtype=float)
    valid = np.isfinite(grid) & (grid >= float(min_tecu))
    n_t = np.where(valid, grid, np.nan) * TEC_TO_NT
    with np.errstate(divide="ignore", invalid="ignore"):
        b_k_hz = np.sqrt(
            (LIGHT_SPEED * float(frequency_hz) ** 3) / (PROPAGATION_COEFF * np.pi * n_t)
        )
    return b_k_hz / 1.0e6
