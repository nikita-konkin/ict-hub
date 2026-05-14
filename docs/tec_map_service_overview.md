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

- if both canonical parquet files and `__dupN` variants exist, the loader keeps one canonical copy per shard stem

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

Interpolation uses SciPy `griddata` on IPP positions:

- primary method: `linear`
- fallback method: `nearest`

Behavior:

- if there are at least 3 samples, try linear interpolation first
- where linear returns gaps, fill with nearest-neighbor interpolation
- if there are fewer than 3 samples, use nearest-neighbor interpolation directly

### Coverage Mask

The map is not allowed to extend infinitely away from the IPP sample cloud.

The service first builds a hard coverage mask using minimum great-circle distance from each grid cell to the nearest IPP:

- default coverage radius: `ipp_gradient_radius_km = 300 km`

Then it softens the binary mask slightly:

- internal mask smoothing in grid-cell units

This is why edges are rounded somewhat instead of being a hard staircase.

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
