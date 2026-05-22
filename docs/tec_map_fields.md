# TEC Map "Field" Modes

The TEC Map service can render different scalar fields derived from VTEC. Pick
one via the `?field=...` query parameter on `/tec-map/gif` and
`/tec-map/snapshot` (the Analysis page exposes the same choice through the
**Field** dropdown).

Only `vtec` (default) and `vtec_gradient` are implemented today. The remaining
variants below are documented as a forward-looking specification: when one is
needed for an investigation, it should be added behind the same `field=` switch
to keep the API uniform.

---

## Implemented

### `vtec` — VTEC magnitude (default)

The classic ionospheric vertical total electron content map. Units: TECU.
Colour scale: project palette (`tec_map_spectrum`, blue → red).

Pipeline:

1. Aggregate per `(frame_time, station)` to one mean IPP point per station.
2. `griddata` linear interpolation, nearest-neighbour fill for the convex-hull
   exterior.
3. NaN-aware Gaussian smoothing (`smoothing_sigma`).
4. Coverage mask: cells within `ipp_gradient_radius_km` of any IPP.
5. Plot as a contour map.

### `vtec_gradient` — |∇VTEC| spatial gradient magnitude

Highlights spatial structure: gradients, fronts, equatorial-anomaly walls,
medium-scale TIDs. Computed *after* smoothing on the same grid as the scalar
VTEC field.

Definition:

```
∂V/∂y_km = ∂V / (Δlat · 111.32)                # km in latitude direction
∂V/∂x_km = (∂V / Δlon_deg) / (111.32 · cos φ)  # km in longitude, latitude-dependent
|∇V|     = sqrt( (∂V/∂x_km)² + (∂V/∂y_km)² )   # TECU/km
field    = |∇V| · 100                          # reported as TECU / 100 km
```

Implementation: `compute_vtec_gradient_magnitude(grid, grid_lon, grid_lat)` in
`app/tec_map_render.py`. Uses `np.gradient` (one-sided differences at the
borders); per-row rescaling for the longitudinal derivative accounts for the
shrinking east-west cell size with latitude.

Colour scale: `magma` (matplotlib) / `Magma` (plotly). Limits: `[0, q95]` of
the gradient values, taken globally across the whole animation so frames stay
on a shared scale.

IPP sample markers are rendered as neutral position dots in this mode (their
per-point VTEC values are not on the gradient colour scale).

Typical magnitudes for context:

| Regime | Approx. `|∇VTEC|` |
| --- | --- |
| Quiet mid-latitude background | 0.5 – 2 TECU / 100 km |
| Equatorial-anomaly crest wall | 5 – 15 TECU / 100 km |
| Storm-time / TID activity | 5 – 30+ TECU / 100 km |

---

## Future variants (specification)

These should reuse the same `field=` switch when implemented. None of them is
wired yet.

### `vtec_gradient_components` — ∂VTEC/∂x and ∂VTEC/∂y

Two side-by-side panels showing the east-west and north-south derivatives
separately, on a *diverging* colour scale (e.g. `RdBu_r`), centred at zero.

Why this is different from `|∇VTEC|`: it preserves *direction* information.
You can see whether a gradient feature is oriented mostly N-S (typical of the
equatorial anomaly crests) or mostly E-W (typical of dusk terminators and
many TIDs).

Implementation sketch:

- Same `compute_vtec_gradient_magnitude` math, but return the signed
  components instead of the magnitude.
- New render path: side-by-side `matplotlib` subplots for GIF, or two stacked
  Plotly heatmaps with a shared colour bar.
- Suggested colour limits: symmetric `±q95(|component|)`.

### `vtec_rate` (a.k.a. ROT) — temporal rate of TEC

dVTEC/dt at each grid cell. Unlike `vtec_gradient`, which is a *spatial*
derivative inside a single frame, this is the difference between consecutive
frames at the same grid cell.

Standard in TID/scintillation studies. ROTI (the standard deviation of ROT
over a sliding window) is the next natural step on top of this.

Implementation sketch:

- After the existing pass-1 loop in `build_animation_gif_bytes` (which already
  builds per-frame grids), compute `grid[t] - grid[t-1]` divided by
  `(frame_time[t] - frame_time[t-1]).total_seconds() / 60.0`.
- Units: TECU/min.
- The first frame has no derivative; render as fully transparent or skip.
- Colour scale: diverging, symmetric. Limits from `±q95(|rate|)` over the
  whole animation.
- For ROTI, add a `roti_window_minutes` config field and apply a rolling-std
  per cell across frames.

### `vtec_anomaly` — detrended VTEC

VTEC minus a large-scale smoothed background, highlighting local deviations.
Common in TID-detection papers and ionospheric-storm post-mortems.

Implementation sketch:

- After NaN-aware Gaussian smoothing with the existing `smoothing_sigma`,
  apply a *second* smoothing pass with a much larger sigma (e.g. configurable
  `background_sigma`, defaulting to ~10× `smoothing_sigma`).
- `anomaly = grid - background_grid`.
- Colour scale: diverging, symmetric around zero. Limits from
  `±q95(|anomaly|)`.
- Note: this is sensitive to the choice of `background_sigma`; it should be a
  separate query param on the route, not hard-coded.

---

## Notes on data-quality improvements that benefit every field

The per-station IPP averaging in `build_frame_summary`
(`app/tec_map_pipeline.py`) collapses every satellite seen from a station to a
single point per frame. For *any* field that depends on spatial structure
(gradient, anomaly), this is the biggest single source of resolution loss. If
gradient maps look blocky or unphysical, the first lever to pull is changing
that aggregation to `(frame_time, station, satellite)` so each IPP becomes its
own interpolation point.
