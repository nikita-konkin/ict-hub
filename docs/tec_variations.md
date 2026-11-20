# TEC Variations (dTEC)

The converter takes tec-suite DAT observations and writes one table per station
and UTC day. Each table locates the observations at their ionospheric pierce
points and carries one variation estimate in TECU per requested period band.
Use `python -m tec_variations --help` for the full CLI.

## Filtering and projection

1. Read the configured TEC column and its validity mask. Break satellite arcs
   at loss of lock, at long gaps and at observations below the elevation cutoff.
   Elevation must be at least 30° by default. Otsuka et al. used 35° to limit
   cycle-slip effects and mapping uncertainty. TEC Variations uses 30° to keep
   more observations while still excluding low-elevation paths.
2. Resample each arc on its reported cadence by linear interpolation across
   bounded gaps, up to two missing samples by default. A compatible track from
   an adjacent day can extend an arc across midnight. The table includes only
   observed samples from the selected UTC day.
3. Subtract a least-squares quadratic in normalized time from the regular slant
   phase TEC. The intercept absorbs the unknown phase ambiguity and the
   quadratic removes smooth curvature without a moving-window boundary.
   Maletckii et al. showed how detrending and band selection can introduce
   artifacts such as spurious long-period structure. Guerra et al. found
   Savitzky–Golay and Fast Iterative Filtering most reliable overall among
   their tested techniques. Here the quadratic is a low-order background
   model and the Butterworth stage supplies the defined band limits.
4. Apply a Butterworth band-pass to the residual, with a fourth-order prototype
   (eighth-order band-pass per pass), in second-order sections. SciPy
   `sosfiltfilt` filters forward and backward, giving zero phase shift and
   squaring the single-pass amplitude response. At each nominal band edge the
   ideal two-pass amplitude ratio is 0.5. The transition is gradual and powers
   in adjacent bands should not be added as if the bands were disjoint.
5. Use odd-reflection padding and mask the same number of samples from each
   output edge. This guard is the largest of one longest-band period, the
   slowest pole's e-folding time, and SciPy's default band-pass pad length
   (27 samples). Require at least three longest-band periods of input and at
   least one full longest-band period after trimming. For the three defaults
   at 30 s cadence the guard at each end is 13.5, 24.5 and 60 minutes.
   These bounds limit transient leakage. They are a policy for finite arcs and
   cannot exclude all ringing caused by sharp disturbances within an arc.
6. Multiply the retained slant variation by the slant-to-vertical factor
   evaluated at each observed epoch using a 300 km thin shell by default.
   Applying this factor to the unknown slant offset before filtering can
   introduce false variations as the elevation changes. Use `--vertical 0`
   to keep the filtered slant variation.

The default bands are 2–10, 10–20 and 20–60 minutes, following SIMuRG.
Use `--bands 2.5-8,10-25` to select custom ranges. Unsupported samples are
null in that band's column. Daily `short_arcs_by_band` counts arcs without a
supported observation on the combined arc. `undersampled_arcs_by_band` counts
arcs whose cadence places the short-period edge at or above Nyquist frequency.

## Legacy mode and optional maps

`--filter legacy` restores the reconstructed MapTEC arc leveling and centered
running means, including the shrinking windows at the edges. Its default
`2–20` minute setting uses `--smoothing-minutes 2 --detrend-minutes 20`. Set
`--bands` to run additional boxcar window pairs. The cutoff still defaults to
30° and can be set to 0° for the earlier elevation selection. The legacy
minimum of 10 TECU and pre-filter edge skip of five samples are configurable.
See the [MapTEC reconstruction history](maptec_reconstruction.md) for the
differences between the reconstructed filter and the original executable.

`--legacy-maps` writes `Map_*.dat` files in the historical column order.
`--png` writes PNGs independently. Both are off by default and use the first
selected band. With the Butterworth filter, the legacy smoothed TEC field is
`nan` because the method yields a variation without an absolute TEC estimate.
For maps, `--di-threshold` defaults to 0.02 TECU, the upper end of the
theoretical carrier-phase precision range given by Otsuka et al. Map epoch,
geographic bounds and threshold settings apply to both map formats. Use
`--world-contour` and `--solar-terminator` with `--png` to draw those overlays.

## Parquet contract for IonMaps

Each successful UTC day has `OUTPUT/YYYY/DDD/SITE/SITE_dtec_DDD_YY.parquet`
for each selected station and a daily `diagnostics.json`. Days use
`[00:00, 24:00)` UTC. Rows are sorted by UTC time and satellite. The arc ID is
local to its station-day. Below-cutoff and invalid observations are excluded.
Every remaining observation gets a row, including observations where no band
has a value and those outside the optional map limits. An individual station
can have an empty table with the same schema. A wholly unreadable or empty
input day yields a failure report. `--overwrite` replaces a complete day,
including its station tables and optional maps.

| Column | Arrow type | Units or meaning |
| --- | --- | --- |
| `datetime_utc` | `timestamp[us, tz=UTC]` | Observation time |
| `station` | `string` | Input station directory name |
| `satellite` | `string` | Uppercase GNSS ID, e.g. `G05` |
| `elevation_deg`, `azimuth_deg` | `float64` | Degrees |
| `ipp_latitude_deg`, `ipp_longitude_deg` | `float64` | Degrees, longitude in [−180, 180) |
| `slant_to_vertical_factor` | `float64` | Dimensionless projection factor |
| `arc_id` | `int32` | One-based arc ID |
| `dtec_2_10m`, `dtec_10_20m`, `dtec_20_60m` | Nullable `float64` | TECU |

Custom periods use the same naming rule with `p` as the decimal separator
(`dtec_2p5_8m` for 2.5–8 minutes). Only the dTEC fields are nullable. Each has
field metadata `units=TECU`. File metadata contains `package_version`,
`shell_height_km`, `bands_minutes` (a JSON array of ordered pairs), `filter`,
`elevation_cutoff_deg`, `projection`, `tec_parameter` and `max_absent_points`.
The legacy filter also records `minimum_tec` and `skip_edge_samples`.

## Viewing in IonMaps

Set `TEC_VARIATIONS_OUTPUT_DATA_PATH_HOST` to the converter output root on the
Docker host. Compose mounts it read-only in the hub at
`TEC_VARIATIONS_OUTPUT_DATA_PATH_CONTAINER` (default `/mnt/tec-variations-out`).
For a hub running directly on the host, set the container-path variable to the
local output root.

On **IonMaps**, select **TEC variations (dTEC)** under **Data**. The available
dates, stations and period bands come from the converter tables. Select a band
and a UTC time for an interactive snapshot. Select a range for a background
GIF, MP4 or WebM render. To cross midnight, enter an end date or two full UTC
timestamps. Clock-only ranges with an earlier end time roll into the next day
when the end date is blank.

Each frame draws the nearest observed sample for each station–satellite pair
within half the frame step. A null band value leaves that pierce point blank.
Frames advance uniformly from the start up to the end time, including empty
frames. The default step is one minute. The symmetric
Auto scale uses the 98th percentile of absolute dTEC in the sample window with
a 0.01 TECU floor. Enter a positive limit to use a fixed ± range across every
frame. The shell height in the table metadata sets the optional terminator
altitude. A request can cover up to 40 stations and 1,440 frames, and selected
tables must use the same shell height.

Future work includes the 0.15° pixel mode with 10-minute and spatial smoothing
described by Otsuka et al. (2013).

## Measured filter response

A 0.4 TECU plane wave with 350 km wavelength was sampled at three stations at
0, 115 and 260 km along the propagation direction. The slant series had a
quadratic background, a 200 TECU phase offset and 12 hours of 30 s samples.
The amplitude ratio is the fitted sine/cosine amplitude on returned samples
divided by 0.4 TECU. Each range spans the three station phases.

| Band (min) | Period (min) | Amplitude ratio |
| --- | ---: | ---: |
| 2–10 | 1.5 | 0.004774–0.004783 |
| 2–10 | 5 | 1.000021–1.000147 |
| 2–10 | 14 | 0.031140–0.031182 |
| 2–10 | 35 | 0.000015–0.000026 |
| 2–10 | 120 | 0.000000–0.000001 |
| 10–20 | 1.5 | 0.000011–0.000033 |
| 10–20 | 5 | 0.000042–0.000179 |
| 10–20 | 14 | 0.999863–1.001325 |
| 10–20 | 35 | 0.000114–0.000175 |
| 10–20 | 120 | 0.000002–0.000014 |
| 20–60 | 1.5 | 0.000057–0.000106 |
| 20–60 | 5 | 0.000007–0.000108 |
| 20–60 | 14 | 0.009283–0.009877 |
| 20–60 | 35 | 0.995161–0.998483 |
| 20–60 | 120 | 0.000450–0.001171 |

A trend-only test with a 200 TECU offset and elevation changing between 35°
and 85° produces peak absolute dTEC below 3 × 10⁻¹⁴ TECU in all new bands.
The legacy window pairs produce 0.020538, 0.086564 and 0.795350 TECU,
respectively, on the same input.

## Archived station-day comparison

The April 30, 2015 (day 120) archive had 220 tracks and no adjacent-day files.
Both filters used `--tec-parameter tec.l`, vertical output and the same 30°
cutoff. The legacy run used `--filter legacy --bands 2-10,10-20,20-60,2-20`.
Both runs produced 104,682 rows with identical observation keys
(tver 26,739, tymn 26,927, ulyn 24,276 and usln 26,740).

| Band (min) | Butterworth valid / RMS (TECU) | Legacy valid / RMS (TECU) | Legacy RMS on Butterworth rows (TECU) |
| --- | ---: | ---: | ---: |
| 2–10 | 84,207 / 0.014851 | 100,356 / 0.019554 | 0.019482 |
| 10–20 | 69,523 / 0.017877 | 100,043 / 0.025356 | 0.025410 |
| 20–60 | 26,203 / 0.045160 | 95,449 / 0.099390 | 0.110748 |
| 2–20 (historical) | — | 100,043 / 0.036307 | — |

Legacy returned a value at every observation where the new filter did. Its
RMS is higher on those same rows in all three bands. The 20–60 minute band
has the largest RMS difference and the least coverage. The new filter reports
333 arcs too short for that band, compared with 129 and 173 for the shorter
bands. This observational comparison measures method differences; the
archive has no independent dTEC ground truth.

## References

- Maletckii, Yasyukevich and Vesnin (2020), *Remote Sensing* 12(8), 1340.
  [Wave signatures in total electron content variations](https://doi.org/10.3390/rs12081340)
- Guerra, Cesaroni, Ravanelli and Spogli (2024), *Journal of Space Weather and Space Climate* 14, 17.
  [Travelling ionospheric disturbances detection](https://doi.org/10.1051/swsc/2024017)
- Otsuka et al. (2013), *Annales Geophysicae* 31, 163–172.
  [GPS observations of medium-scale traveling ionospheric disturbances over Europe](https://doi.org/10.5194/angeo-31-163-2013)
- Yasyukevich et al. (2020), *GPS Solutions* 24, 69.
  [SIMuRG](https://doi.org/10.1007/s10291-020-00983-2)
- SciPy v1.13.1. [Butterworth design](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.signal.butter.html)
  and [forward-backward SOS filtering](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.signal.sosfiltfilt.html)
