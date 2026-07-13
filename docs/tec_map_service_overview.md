# TEC Map Service Overview

This service turns precomputed TEC-suite parquet outputs into VTEC map products inside `ict-hub`.

Scope:

- It reads TEC-suite data only from `.parquet`.
- It does not run TEC-suite.
- It does not parse `.dat` files directly.

The service supports two output modes:

- `GET /tec-map/gif` -> animated `image/gif`
- `GET /tec-map/snapshot` -> Plotly figure JSON

## What The Service Does

At a high level, the TEC map flow is:

1. Load TEC link data from parquet for one or more stations.
2. Normalize timestamps, station metadata, geometry, and TEC observable columns.
3. Build per-station, per-satellite continuous arcs.
4. Level phase TEC onto code TEC.
5. Apply receiver bias correction.
6. Convert slant TEC (STEC) to vertical TEC (VTEC).
7. Compute ionospheric pierce point (IPP) latitude/longitude.
8. Aggregate data into time frames.
9. Interpolate VTEC over a lon/lat grid.
10. Smooth the grid and apply a coverage mask.
11. Render either a GIF frame sequence or a Plotly snapshot.

## Endpoints

### GIF Range Mode

`GET /tec-map/gif`

Main inputs:

- `date=YYYY-MM-DD` or `year=YYYY&doy=DDD`
- `end_date=YYYY-MM-DD` optional for multi-day GIFs
- repeatable `stations=...`
- `start_time=...`
- `end_time=...`

Time input rules:

- `start_time` and `end_time` can be full UTC timestamps such as `2026-01-02T03:00:00Z`
- or clock strings such as `03:00:00`
- clock strings are combined with the chosen day or day range

Multi-day behavior:

- If `end_date` is omitted, GIF mode renders a single UTC day.
- If `end_date` is set, the backend loads each UTC day in the range separately and combines them into one animation.

Output:

- `image/gif`

### Snapshot Mode

`GET /tec-map/snapshot`

Main inputs:

- `date=YYYY-MM-DD` or `year=YYYY&doy=DDD`
- repeatable `stations=...`
- `timestamp=...`

Output:

- Plotly figure JSON suitable for `Plotly.newPlot(...)`

Snapshot semantics:

- The snapshot uses the frame bucket that contains the requested timestamp.
- Internally this is `datetime.floor(frame_minutes)`.

### Per-Station Series Export

`GET /tec-map/series`

One file with the time series of the selected field for every requested
station over the requested range: one row per `(frame_time, station)` — the
same frame aggregation the map is built from (values at station IPPs, before
any spatial interpolation, so gridding/interpolation parameters do not apply).

Main inputs:

- period/stations/pipeline parameters as in GIF range mode
  (`min_elevation_deg`, `frame_minutes`, `ionosphere_height_km`,
  `vtec_smooth_epochs`, `normalize_stations`, ...)
- `field=vtec|gdd|b_k` (+ `signal_band` for gdd/b_k); `vtec_gradient` is
  rejected — it is a spatial field of the interpolated map
- `format=csv` (default) or `json`

Output columns: `frame_time, station, site_lat, site_lon, ipp_lat, ipp_lon,
samples, vtec_tecu` plus `gdd_ns_per_ghz` or `b_k_mhz` for derived fields.
The IonMaps UI exposes this as the "Download station series (CSV)" button,
which reuses the current TEC Map form values.

## Frontend/Auth Notes

- Requests use ict-hub session auth.
- Browser `fetch` calls must include cookies.
- `stations` is a repeatable query parameter.

Example:

```js
const params = new URLSearchParams({
  date: "2026-01-03",
  timestamp: "13:00:00",
  frame_minutes: "15",
});
["aksu", "alex", "arsk"].forEach((st) => params.append("stations", st));

const res = await fetch(`/tec-map/snapshot?${params.toString()}`, {
  credentials: "include",
});
```

## Parquet Contract

The loader accepts TEC-suite style columns or already-normalized columns.

Required logical fields:

- `datetime`, or enough information to reconstruct it from:
  - `tsn` plus parquet metadata `interval_seconds`
  - or `hour`
- `station`
- `satellite`
- receiver position:
  - `site_lon`, `site_lat`
  - or `site_x`, `site_y`, `site_z`
- link geometry:
  - `elevation_deg`
  - `azimuth_deg`
- TEC observables:
  - `phase_tec`
  - `code_tec_p1p2` preferred when present
  - `code_tec_c1p2` fallback when present
- `validity`

Optional:

- `sat_x`, `sat_y`, `sat_z`

Common TEC-suite aliases are mapped automatically:

- `site.l` -> `site_lon`
- `site.b` -> `site_lat`
- `site.x/site.y/site.z` -> `site_x/site_y/site_z`
- `sat.x/sat.y/sat.z` -> `sat_x/sat_y/sat_z`
- `el` -> `elevation_deg`
- `az` -> `azimuth_deg`
- `tec.l1l2` -> `phase_tec`
- `tec.p1p2` -> `code_tec_p1p2`
- `tec.c1p2` -> `code_tec_c1p2`

Recommended parquet metadata:

- original DAT header lines under `dat_parquet_handler.header_lines`

That metadata is used to recover:

- site coordinates
- sample interval
- custom datetime formatting

## Storage Layout

Default lookup layout:

- `<DATA_ROOT>/<YYYY>/<DDD>/<station>/*.parquet`

Also supported:

- `<DATA_ROOT>/<YYYY>/<DDD>/<station><DDD>0/*.parquet`

Duplicate shard handling:

- the loader reads every shard including `__dupN` variants: session (re-upload) RINEX
  archives produce shards whose epochs complement the daily file, so dropping variants
  would lose data
- exact duplicates are removed at the row level — one row is kept per
  `(station, satellite, datetime)` after concatenation

## VTEC Calculation

### 1. Raw Link Loading

The backend loads all requested station parquet shards for the selected day, or for each day in a GIF day range.

Filtering happens as early as possible:

- time predicate pushdown on `tsn` when possible
- otherwise on `hour`
- final exact time filtering after load
- minimum elevation filtering

### 2. Phase-To-Code Leveling

Leveling is done independently for each `station + satellite` group.

The code observable is chosen as:

- prefer `code_tec_p1p2` when its magnitude is non-trivial
- otherwise fall back to `code_tec_c1p2`

Arc resets happen when:

- the time gap exceeds `sampling_interval_seconds * 1.5`
- `validity != 0`
- `phase_tec` is effectively absent

For each arc:

- if at least 3 good phase/code samples exist, the backend computes `median(code_tec - phase_tec)`
- that median becomes the arc bias
- `stec_tecu = phase_tec + arc_bias`

If an arc does not have enough good samples, the code TEC path is retained.

### 3. Receiver Bias Correction

After arc leveling, the service applies receiver bias correction.

Current implementation:

- MSTD receiver bias estimation
- GPS-focused default behavior
- transmitter bias defaults to zero

Not implemented here:

- external DCB catalogs
- RINEX bias products
- transmitter bias catalogs

### 4. Mapping STEC To VTEC

The mapping factor is computed from elevation and the assumed thin-shell ionosphere height:

- shell height parameter: `ionosphere_height_km`
- Earth radius is fixed internally

The backend computes:

- `mapping_factor = 1 / sqrt(1 - sin(chi)^2)`
- `vtec_tecu = stec_tecu / mapping_factor`

Raw intermediate values are also kept internally:

- `stec_tecu_raw`
- `vtec_tecu_raw`

Non-negative output behavior:

- by default, negative VTEC is clipped to `0`
- final VTEC is constrained to a sane range before rendering

## IPP Calculation

For each leveled link, the backend computes the ionospheric pierce point using:

- receiver latitude/longitude
- elevation
- azimuth
- `ionosphere_height_km`

The result is:

- `ipp_lat`
- `ipp_lon`

These IPP coordinates are what drive the map interpolation, not the raw station locations.

## Frame Aggregation

After VTEC is computed, data is grouped into time frames:

- `frame_time = floor(datetime, frame_minutes)`

Within each `frame_time + station` bucket, the service aggregates:

- station location: first value
- IPP latitude/longitude: mean
- VTEC: mean
- sample count: size

This means the plotted point layer is already a per-frame, per-station summary rather than every raw sample.

## Interpolation And Smoothing

### Grid Construction

The renderer creates a regular lon/lat grid over the frame bounds.

Bounds are expanded from the IPP cloud by about 2 degrees on each side.

Grid density is controlled by:

- `grid_resolution_deg`

Smaller values give finer grids and smoother-looking maps, but increase CPU, memory, and GIF size.

### VTEC Interpolation

The interpolation principle is selected per request via `interpolation`
(query parameter on `/tec-map/gif`, `/tec-map/snapshot`, `/tec-map/frame`):

**`interpolation=linear` (default)** — SciPy `griddata` on IPP positions:

- if there are at least 3 samples, Delaunay-linear interpolation first
- where linear returns gaps (outside the convex hull), fill with
  nearest-neighbor interpolation — note this produces flat plateaus that
  extend the nearest sample value; interpret map edges accordingly
- if there are fewer than 3 samples, use nearest-neighbor directly

**`interpolation=kriging`** — ordinary kriging on the sphere
(`app/tec_map_kriging.py`):

- an exponential variogram `γ(h) = nugget + sill·(1 − exp(−h/range))` is
  fitted to each frame's own samples (binned empirical semivariogram,
  `scipy.optimize.curve_fit`); on failure it falls back to literature-based
  defaults — range 300 km, nugget 5% of the sample variance
- the nugget effect de-weights noisy individual samples instead of passing
  through them exactly
- away from the sample cloud the prediction relaxes towards the field mean —
  no nearest-neighbour plateaus
- frames with fewer than 8 samples fall back to the linear path
- cost: one (n+1)×(n+1) solve per frame (n = samples per frame, typically
  30–100) — render time is essentially unchanged

**`interpolation=lpi`** — local polynomial interpolation
(`app/tec_map_lpi.py`; aliases: `local_polynomial`, `loess`, `lwr`):

- moving weighted least squares: at every grid node a degree-1 polynomial
  (local plane) is fitted to the frame's samples with Gaussian distance
  weights, `w = exp(−½(d/σ)²)`, σ = 200 km — between the mid-latitude TEC
  decorrelation length (80–130 km) and the 300 km coverage radius
- `lpi_degree=2` (query parameter, default 1) fits a local quadric instead —
  tracks curvature (e.g. the midday TEC bump) better, but only at nodes with
  at least 7 effective neighbours (weight ≥ 0.1, i.e. within ~2.1σ); sparser
  nodes silently drop to the degree-1 plane, so the boundary behaviour never
  degrades. Degree 2 is more sensitive to residual station biases
- the local basis is centred at the target (intercept = prediction) and a
  scale-aware ridge on the slope terms shrinks the plane towards a weighted
  mean when the neighbourhood geometry is degenerate (e.g. collinear
  stations) — no runaway gradients
- targets far outside the sample cloud fall back to the nearest sample
  (the coverage mask hides them anyway)
- frames with fewer than 4 samples fall back to the linear path
- comparative studies (Ogryzek et al., 2020) rank LPI and ordinary kriging as
  the two most accurate local methods, LPI marginally ahead in quiet
  conditions; cost: one batched 3×3 solve per grid node — negligible

### Coverage Mask

The map is not allowed to extend infinitely away from the IPP sample cloud.

The service builds a hard coverage mask using minimum great-circle distance from each grid cell to the nearest IPP:

- default coverage radius: `ipp_gradient_radius_km = 300 km`

Order of operations (since July 2026): the field is interpolated, smoothed and
(for derived fields) transformed on the base grid **without** the coverage
cut; it is then bilinearly upsampled by `upsample` (default 2), and the mask
is evaluated at that render resolution and applied last. Because the mask is
the exact union of 300-km circles computed on the fine grid, the field edge
follows smooth circle arcs instead of the stair-stepped outline of coarse
grid cells (`upsample=1` reproduces the old coarse-cell edge).

A slight Gaussian softening of the binary mask (in grid-cell units, scaled by
the upsample factor) additionally anti-aliases the boundary.

### Gaussian Smoothing

After interpolation, the grid is Gaussian-smoothed.

Parameter:

- `smoothing_sigma`

Meaning:

- `smoothing_sigma` is the Gaussian sigma in grid-cell units, not kilometers
- larger sigma gives a softer, more blended field
- smaller sigma preserves local structure and sharp gradients

Practical interpretation:

- `0` -> no smoothing
- around `1` -> light smoothing
- `2` to `4` -> visibly broader, more blended gradients
- too large -> oversmoothed maps that can hide local variation

Important:

- `smoothing_sigma` smooths VTEC values inside the covered region
- it does not create data outside the coverage mask

## Rendering Techniques

### Shared Color Scaling

Both renderers derive their color limits from the current selection:

- lower bound: 5th percentile of `vtec_tecu`
- upper bound: 95th percentile of `vtec_tecu`

If the range collapses, the backend expands it slightly so the scale remains usable.

### GIF Rendering

GIF mode is rendered fully server-side.

Rendering path:

1. Build one VTEC grid per frame.
2. Render each frame with Matplotlib.
3. Optional OpenStreetMap basemap is drawn first.
4. The VTEC field is drawn with `contourf`.
5. Station markers and IPP markers are overlaid.
6. Each frame is encoded to PNG.
7. PNG frames are quantized to 256-color paletted frames.
8. Frames are streamed into the final GIF with per-frame local color tables.

Why GIFs can still look different from snapshots:

- GIF is limited to 256 colors per frame
- quantization is necessary
- the basemap and VTEC layer share that palette budget

Memory behavior:

- multi-frame GIF assembly is streamed frame-by-frame
- this avoids holding the entire animation as full-resolution images in RAM at once

### Snapshot Rendering

Snapshot mode returns Plotly JSON.

Rendering path:

1. Build one interpolated grid for the selected frame bucket.
2. Convert `NaN` cells to `null` for JSON safety.
3. Render the VTEC surface as a Plotly `Heatmap`.
4. Apply `zsmooth="best"` for a continuous look.
5. Overlay station markers and IPP markers.

Current snapshot characteristics:

- no basemap layer in Plotly mode
- smoother-looking continuous color transitions than the GIF path

### Basemap Rendering

Basemap support currently applies to GIF rendering.

Implementation details:

- OpenStreetMap tiles
- on-disk cache under `/app/data/basemap_cache`
- automatic zoom choice constrained by `basemap_max_tiles`
- geographic data is projected to Web Mercator when basemap is enabled

## Advanced Options

### `min_elevation_deg`

Minimum satellite elevation angle to keep.

Why it matters:

- low-elevation links are often noisier
- raising this value usually improves stability
- raising it too much reduces coverage

### `sampling_interval_seconds`

Expected cadence of the TEC series.

Why it matters:

- it is used when deciding whether a gap breaks an arc
- larger values tolerate bigger time gaps before resetting phase leveling
- smaller values make arc splitting stricter

### `frame_minutes`

Time bin size for output frames.

Why it matters:

- smaller values -> more frames, less temporal averaging, larger GIFs
- larger values -> fewer frames, more temporal smoothing, smaller GIFs

### `ionosphere_height_km`

Thin-shell ionosphere height used in:

- STEC -> VTEC mapping
- IPP geometry

Why it matters:

- changing it moves IPP locations
- changing it also changes the mapping factor and therefore VTEC estimates

### `grid_resolution_deg`

Interpolation grid spacing in degrees.

Why it matters:

- smaller grid spacing -> finer detail and smoother edges
- larger grid spacing -> faster rendering but blockier surfaces

### `smoothing_sigma`

Gaussian smoothing strength in grid cells.

Why it matters:

- larger values make the field softer and more continuous
- smaller values preserve sharper gradients

### `basemap`

Enable OpenStreetMap tiles for GIF mode.

Tradeoffs:

- makes geographic context easier to read
- increases render cost
- can reduce apparent color intensity because the VTEC layer is blended over map tiles

### `field` and `signal_band`

`field` selects the scalar rendered on the map. All fields start from the same
smoothed VTEC grid; derived fields are computed per frame after smoothing:

- `vtec` (default) — VTEC magnitude [TECU]
- `vtec_gradient` — spatial-gradient magnitude |∇VTEC| [TECU / 100 km]
- `gdd` — group delay dispersion magnitude |D| [ns/GHz], pointwise transform
  `|D| = 3·80.5·N_t / (2·c·π·f³)` with `N_t = VTEC·10¹⁶`
- `b_k` — coherence bandwidth [MHz], `B_k = sqrt(c·f³ / (80.5·π·N_t))`;
  cells with VTEC < 0.1 TECU are masked (B_k diverges as TEC → 0)

`signal_band` picks the carrier frequency `f` for `gdd`/`b_k`
(`gps_l1` default; also `gps_l2`, `gps_l5`, `glonass_l1`, `glonass_l2`,
`glonass_l3`, `galileo_e1`, `galileo_e5a`, `galileo_e5b`, `galileo_e5`,
`bds_b1i`, `bds_b1c`, `bds_b2a`, `bds_b2i`). GLONASS L1/L2 are FDMA; the
table uses the k=0 centre frequencies (1602.0 / 1246.0 MHz), L3 is CDMA
(1202.025 MHz).
Formulas and constants mirror `tec-stat/app/services/propagation.py`
(implementation: `app/tec_map_fields.py` — keep the two in sync).

Because `gdd`/`b_k` are pointwise transforms of VTEC, per-IPP markers are
drawn on the same colour scale as the field; for `vtec_gradient` they are
position-only neutral dots.

### Output quality controls (`format`, `quality`, `upsample`, `frame_dpi`, `color_min`/`color_max`)

Animation endpoint (`/tec-map/gif`):

- `format=gif|mp4|webm` — container for the animation. `mp4` (H.264, CRF 18)
  and `webm` (VP9, CRF 30) keep full 24-bit colour (no GIF palette banding)
  and are typically several times smaller than the equivalent GIF for long
  ranges. Requires `imageio` + `imageio-ffmpeg` (bundled ffmpeg binary).
- `quality=standard|high` — `high` switches GIF quantization from FASTOCTREE
  to an adaptive MEDIANCUT palette with Floyd–Steinberg dithering (smoother
  gradients, slower encode) and disables the automatic DPI reduction applied
  to long standard-quality GIF ranges. Video formats always keep full DPI.
- `upsample=1..4` (default 2) — render-grid upsampling. The field is
  interpolated/smoothed on the base grid, bilinearly upsampled, and the
  coverage mask is then evaluated **at render resolution** as the exact
  great-circle union of 300-km circles around the IPPs. This keeps the field
  edge a smooth scalloped curve instead of the stair-stepped outline of
  coarse grid cells. Purely a rendering refinement — no new data is invented.
- `frame_dpi=50..300` — explicit render DPI (default 120 with automatic
  reduction for long standard GIFs).
- `color_min` / `color_max` — explicit colour-scale limits in the field's
  units. Overrides the quantile-based limits; use to keep several exports
  (e.g. different days) on an identical colour scale.

### Static frame export (`/tec-map/frame`)

Publication-quality single frame — same parameters as `/tec-map/snapshot`
plus `dpi` (50–600, default 200), `image_format=png|svg`, `upsample`,
`basemap` and `color_min`/`color_max`. Returns the frame rendered through the
same Matplotlib pipeline as one animation frame. Intended for article figures
(300–600 dpi PNG for print, SVG for vector post-processing).

### Accuracy validation — LOSO cross-validation (`/tec-map/validate`, `show_accuracy`)

Implements map-quality criterion #1 "accuracy at reference points": for every
frame each station is excluded in turn, the field is predicted at its IPP from
the remaining stations (same interpolation dispatch as the map itself,
`app/tec_map_validation.py`), and the prediction error is the leave-one-station-out
(LOSO) accuracy. Errors are always in TECU on the VTEC field — derived fields
(gdd, b_k) are deterministic transforms of VTEC.

`GET /tec-map/validate` takes the same period/station/pipeline parameters as
`/tec-map/gif` plus:

- `interpolation=linear|kriging|lpi|both|all` (default `all` — validates all
  three methods side by side; `both` = linear+kriging; kriging fits the
  variogram once per frame and reuses it for every leave-one-out subset)
- `format=json|csv` — JSON returns `overall` / `per_station` / `per_frame`
  bias-MAE-RMSE summaries; CSV returns the flat per-point table
  (`frame_time, station, vtec_obs, vtec_pred, error, n_train, in_coverage`)

Points whose excluded IPP falls outside the 300 km coverage radius of the
remaining stations are flagged `in_coverage=false` and excluded from the
headline metrics (the rendered map masks those areas out anyway); their count
is reported separately. Frames with fewer than 4 stations are skipped.

`show_accuracy=true` on `/tec-map/gif`, `/tec-map/snapshot` and
`/tec-map/frame` annotates each rendered frame with its LOSO accuracy
("LOSO RMSE 0.82 TECU (n=9)") in the top-left corner — one LOSO pass per frame
at render time. In the Analysis UI: the "Show accuracy on map" select and the
"Validate accuracy (LOSO)" button (renders the per-station comparison table
for linear vs kriging).

`show_params=true` (same three endpoints) prints the map-model constants as a
caption line under the map: grid step, smoothing σg, frame length ΔT, h_ion,
elevation cutoff, coverage radius, interpolation method, plus temporal median /
station normalization / upsampling when active. The UI enables it by default
("Show model parameters").

Literature targets for mid-latitude regional maps: cross-validation RMSE
0.5–1 TECU in quiet conditions, 1.5–2 TECU in disturbed conditions
(Ogryzek et al., 2020). Note the relative-VTEC caveat: LOSO validates the
internal consistency of the field and the interpolation quality, not the
absolute calibration (no external DCB catalogs in ict-hub).

### IonMaps page and station grouping

The map-building UI lives on its own page, `GET /ionmaps` (template
`ionmaps.html`, moved out of the Analysis page in July 2026). It is gated by
the same "analysis" page permission as the `/tec-map/*` endpoints, so no user
access changed.

`GET /tec-map/station-positions?year&doy` (or `date=`) returns receiver
lat/lon for every station with parquet data on that day — read from tec-suite
parquet header metadata only, no data scan; results are cached per day
(`refresh=true` bypasses). It also returns `groups`: greedy proximity
clusters (default `group_radius_km=300`) ordered west-to-east, each with an
anchor station and centre coordinates. The IonMaps "Available parquet data"
block renders station chips grouped by these regions with one-click
whole-group selection; if the endpoint fails the UI falls back to the flat
chip list.

### Request guard rails (GIF)

- multi-day ranges are processed **day by day** (load → level → frame summary),
  so peak memory is bounded by one day regardless of the range length
- station names that exist in none of the requested days → HTTP 400
- more than 40 stations per request → HTTP 413
- more than 800 estimated frames → HTTP 413 (shorten range or raise `frame_minutes`)

## Performance Notes

- Frame count scales with total time span divided by `frame_minutes`.
- Grid size scales roughly with map area divided by `grid_resolution_deg`.
- Multi-day GIFs with small `frame_minutes` can become very large.
- `basemap=true` adds tile fetch/cache work and can increase GIF encoding cost.

## Known Behavior Differences Between GIF And Snapshot

- GIF uses Matplotlib + paletted GIF encoding.
- Snapshot uses Plotly JSON and continuous browser rendering.
- Snapshot can look smoother because it is not restricted to a 256-color GIF palette.
- GIF can look softer or flatter when many colors are spent on the basemap and labels.
