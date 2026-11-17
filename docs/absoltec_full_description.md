# AbsolTEC reconstruction — full description and implementation

**Status: experimental, runnable, scientifically verified against the published method; numeric parity with the 2017 binary not yet achieved.**

This document is the complete reference for the native AbsolTEC reconstruction
(`absoltec_native/`). It describes what the original program does, the recovered
and now literature-verified algorithm, the full implementation (including the
TuRBOTEC science upgrades applied on 2026-11-17/18), and the validation status.

Companion documents:

- [absoltec_reconstruction.md](absoltec_reconstruction.md) — the project's
  working reconstruction notes and evidence log.
- [absoltec_sequence.md](absoltec_sequence.md) — Mermaid sequence diagrams of
  the batch run and the fitting algorithm.
- [AbsolTEC TEC mapping science review.md](../reports/AbsolTEC%20TEC%20mapping%20science%20review.md) —
  the literature review that verified the method and produced the upgrade list.

---

## 1. What AbsolTEC does

AbsolTEC (the 2017 `absolTEC.exe`, alias TayAbsTEC) converts per-satellite
tec-suite DAT files of one GNSS station-day into an absolute vertical TEC map
**over the station**: for every output epoch it estimates the vertical TEC and
its spatial gradients and time derivatives, together with one combined
satellite/receiver differential code bias (DCB) per observed satellite link.

| | |
|---|---|
| Input | `ROOT/YYYY/DDD/SITE/*.dat` — tec-suite satellite files: `tsn, hour, el, az, tec.l1l2, tec.c1p2 (or tec.p1p2), validity` |
| Output | eight-column TEC DAT (`UT, I_v, G_lon, G_lat, G_q_lon, G_q_lat, G_t, G_q_t`), DCB DAT, combined-bias JSON, `diagnostics.json` |
| Model | second-order Taylor expansion of vertical TEC in space and time, plus one daily combined bias per satellite link |
| Constellations | GPS and GLONASS |

The old executable was a 843,776-byte x86 PE (linker timestamp 2017-04-24,
SHA-256 `31984eb0df935b52751cf92458b4471ca8260a98fed9d5fb6492fe175d3443f5`),
reverse-engineered with REA 6.9.0 / Ghidra 12.2. It is not invoked, patched or
calibrated against; this reconstruction is an independent reimplementation.

## 2. The algorithm (recovered, and verified published)

The recovered algorithm is the published Irkutsk-group single-station method —
the TayAbsTEC / TuRBOTEC family of Yasyukevich, Mylnikova and co-workers
(ISTP SB RAS). The open-access successor paper, Yasyukevich, Mylnikova &
Vesnin, *Sensors* 20(19):5702, 2020, DOI 10.3390/s20195702, contains the model
almost verbatim; the reconstruction implements its Equations 3, 4, 6, 8, 9 and
stage 4 leveling.

### 2.1 Measurement model (TuRBOTEC Eq. 3)

```text
slant_TEC = S * [V + G_lon*dlon + G_lat*dlat + Q_lon*dlon^2
                  + Q_lat*dlat^2 + G_t*dt + Q_t*dt^2] + B_satellite
```

- `V` — absolute vertical TEC at the station at epoch `t0`;
- `G_lon, G_lat, Q_lon, Q_lat` — first and second spatial gradients of vertical
  TEC w.r.t. longitude/latitude offset of the ionospheric pierce point (IPP);
- `G_t, Q_t` — first and second time derivatives;
- `B_satellite` — combined satellite+receiver DCB of the link, constant over
  the day;
- mixed derivatives are neglected, as in the paper.

The eight output columns are exactly `UT, I_v, G_lon, G_lat, G_q_lon, G_q_lat,
G_t, G_q_t`; the quadratic values are polynomial coefficients (no hidden 1/2),
in TECU/degree² and TECU/hour².

### 2.2 Mapping function (Eq. 4) and IPP geometry

```text
S = 1 / sqrt(1 - sine^2),   sine = R_E/(R_E + h_max) * sin(alpha*(90 - el))
R_E = 6371 km,  h_max = 450 km,  alpha = 0.97 (mid-latitude)
```

`S` is the modified single-layer model (MSLM) slant factor with the correcting
coefficient α inside the sine argument. The paper tabulates α per station
(TuRBOTEC Table 1) and states that it should be latitude-dependent. The
original table, reproduced in full:

**TuRBOTEC Table 1. Position of GNSS stations.**

| Station | Lat, ° | Lon, ° | MLat, ° | MLon, ° | α |
|---|---:|---:|---:|---:|---:|
| IRKJ | 52.2 | 104.3 | 47.7 | 178.3 | 0.97 |
| NTUS | 1.3 | 103.7 | −7.2 | 176.3 | 0.87 |
| THU2 | 76.5 | 291.2 | 83.8 | 27.1 | 0.94 |

(MLat/MLon are geomagnetic coordinates; the coefficient α is the MSLM
correcting parameter for the modified single-layer model mapping function,
section 2.2.)

The paper's rationale: by its physical implication, α "adjusts a sharp
non-physical TEC growth at low elevations, less than 30°" (its Figure 4
compares α = 1, 0.97, 0.94, 0.87 against IRI-2012 slant TEC). It further notes
α should also depend on the ionosphere disturbance level, and that for the
high-latitude THU2 station the IRI-plas comparison worked better with
α = 0.96 than with the IRI-2012-tuned 0.94 — i.e., the tabulated values are
model-tuned point estimates, not universal constants. The IPP is computed on the spherical shell (6371 km / 450 km);
the published path uses the quadrant-safe `atan2` longitude, the legacy path
reproduces the binary's `asin` form.

### 2.3 Window weighting (Eq. 6)

```text
w = theta(t0 - t + dt) * theta(t + dt - t0) * 1/S * [1 + ((t - t0)/1h)^2]^-1
```

Strict ±1-hour windows around every output epoch (the legacy code excludes the
edges exactly), slant weight `1/[S*(1+dt^2)]`: the `1/S` factor makes
high-elevation measurements dominate and puts the residual approximately in
vertical-TEC space; the time factor localizes the estimate.

### 2.4 Phase-code leveling (stage 4)

Each continuous arc (phase usable above 10° elevation, split at gaps > 124 s,
slips and invalid phase) receives one additive offset that levels phase TEC
onto code TEC. The published path uses the TuRBOTEC elevation-weighted mean:

```text
offset = sum[(code - phase)/S] / sum[1/S]
```

(Note: the printed paper formula contains a stray factor and is not
dimensionally consistent for a constant `S`; the weighted mean above is the
consistent form and what is implemented.)

### 2.5 Combined biases and the reporting gauge (Eq. 9)

Only the sum of satellite and receiver DCB is estimable from one station; the
fit estimates one combined bias per satellite link per day. The split into
satellite and receiver values is a **post-fit** reporting convention with a
zero mean within each constellation ("following Schaer", Eq. 9). It does not
constrain the fit.

### 2.6 Non-negativity bounds (Eq. 8, `--nonneg`)

```text
I_V(t0) > C          for every output epoch,       C = 0.5 TECU
I_DCB,j < min(I_Exp),j - C   for every satellite j
```

Bounded-variable least squares on the consistent 24 h system. The paper used
`scipy.optimize.lsq_linear` (Start–Parker bvls) and reported that about 9% of
epochs change by more than 1 TECU compared with the unconstrained solve, which
otherwise produces physically impossible negative slant TEC.

## 3. Architecture and data flow

```
CLI (absoltec_native/__main__.py)
  discovery + validation + thread pool
      └─ _run_station            lock, input identity, transaction, publish
           ├─ io.read_dat        one SatelliteData per file (validity, breaks)
           └─ solver.reconstruct
                ├─ prepare_observations
                │    ├─ legacy.py:prepare_legacy   (--model legacy-2017)
                │    └─ solver:_prepare_modern     (--model published)
                │         leveling → geometry → _assemble_observations
                └─ fit_prepared
                     ├─ unconstrained: per-window QR + satellite Schur system
                     └─ _fit_bvls (--nonneg): sparse bounded LS, Eq. 8
           └─ io.write_tec_output, bias.write_dcb_output, diagnostics.json
```

| Module | Role |
|---|---|
| `absoltec_native/__main__.py` | CLI, argument checks, station discovery, locks, input hashing, transactional publish, failure records |
| `absoltec_native/io.py` | tec-suite DAT reader (validity bits, slip/gap breaks), eight-column output writer/reader |
| `absoltec_native/geometry.py` | MSLM mapping factor, IPP geometry, longitude offset, TuRBOTEC latitude-dependent α |
| `absoltec_native/legacy.py` | recovered 2017 screening/repair/leveling behavior and window eligibility |
| `absoltec_native/solver.py` | preparation, windowed weighted LS with QR + Schur elimination, bounded-variable LS (`--nonneg`) |
| `absoltec_native/bias.py` | post-fit zero-mean bias split per constellation, DCB DAT writer |

## 4. Implementation details

### 4.1 Input reading — `io.py`

[`read_dat`](N:/ict-hub/absoltec_native/io.py:93) parses one satellite DAT:
headers (`Satellite`, `Site`, `Position (L, B, H)`, `Interval`, `Columns`),
numeric rows, and the tec-suite validity bitmask. Missing measurements are
identified by validity bits, never by a zero placeholder (zero TEC is a valid
measurement):

- phase valid: bits `0b11000` clear (missing L1/L2);
- code valid: `0b1000100` clear for `tec.c1p2`, `0b1100000` for `tec.p1p2`;
- cycle slip: bits `0b11` set;
- arcs additionally split at time gaps > 1.5× the declared cadence.

[`write_tec_output`](N:/ict-hub/absoltec_native/io.py:304) publishes the
eight-column DAT atomically (temp file + exclusive hard link); unsolved epochs
stay NaN so gaps are distinguishable from valid zero TEC.

### 4.2 Geometry — `geometry.py`

- [`mapping_factor`](N:/ict-hub/absoltec_native/geometry.py:19): Eq. 4,
  parameterized by `correction` (α), `shell_height_km`, `earth_radius`. The
  published path uses 6371 km/450 km; the legacy path keeps the recovered
  6371302.0 m/450 km literals.
- [`pierce_point`](N:/ict-hub/absoltec_native/geometry.py:42): spherical-shell
  IPP latitude and longitude; `legacy_longitude=True` reproduces the binary's
  `asin` form (loses the polar quadrant), otherwise the quadrant-safe `atan2`
  form is used.
- [`longitude_offset`](N:/ict-hub/absoltec_native/geometry.py:86): shortest
  signed longitude difference (dateline-safe).
- [`turbotec_alpha`](N:/ict-hub/absoltec_native/geometry.py:91): latitude-
  dependent α — 0.87 for |lat| < 25°, 0.97 for 25–65°, 0.94 for |lat| > 65°,
  reproducing TuRBOTEC Table 1 (§2.2). The paper tabulates three stations
  rather than band boundaries; these thresholds place its stations inside
  their bands (NTUS 1.3° → 0.87, IRKJ 52.2° → 0.97, THU2 76.5° → 0.94).

### 4.3 Published preparation — `solver.py:_prepare_modern`

[SolverConfig](N:/ict-hub/absoltec_native/solver.py:31) holds every tunable:

```text
time_step_hours (0.5)        elevation_cutoff (10°)     correction_coefficient (0.97)
model (legacy-2017|published) window_hours (1.0)        min_arc_samples (21)
min_window_samples (20)      min_satellites (6)         condition_limit (1e10)
auto_mapping_alpha (False)   mapping_shell_height_km (450)   nonneg (False)
```

[`_prepare_modern`](N:/ict-hub/absoltec_native/solver.py:121):

1. budget and identity checks (observations, satellites, one station,
   consistent coordinates);
2. arc screening at 10° elevation using validity/break flags (missing code
   does not split an arc; slips, missing phase and gaps do);
3. **1/S elevation-weighted leveling** (section 2.4) per arc with the effective
   α (explicit value, or `turbotec_alpha(lat0)` when `auto_mapping_alpha`);
4. arcs shorter than `min_arc_samples` or without two code anchors are
   rejected.

[`_assemble_observations`](N:/ict-hub/absoltec_native/solver.py:218) computes
IPP geometry, `S`, `dlon`, `dlat`, applies the offsets, sorts by hour and
returns [`PreparedObservations`](N:/ict-hub/absoltec_native/solver.py:93)
(hour, leveled slant TEC, mapping, dlon, dlat, satellite index, ids,
diagnostics).

### 4.4 Legacy preparation — `legacy.py`

[`prepare_legacy`](N:/ict-hub/absoltec_native/legacy.py:129) replays the
recovered 2017 behavior for parity:

- [`_legacy_arrays`](N:/ict-hub/absoltec_native/legacy.py:23): rows with
  both phase and code zero are dropped, only 30-second epochs are kept, at
  most 1999 samples per file (zero sentinels around the buffer);
- [`_arcs`](N:/ict-hub/absoltec_native/legacy.py:45): spans of non-zero phase
  above 10°, tolerating interruptions ≤ 124 s; the validity mask is **not**
  consulted (recovered quirk);
- [`_repair_arcs`](N:/ict-hub/absoltec_native/legacy.py:57): scalar spike
  repairs with the recovered 4σ / 5-TECU recurrence and the phase-tail shift
  (the literature argues against the tail shift — kept for parity only);
- leveling keeps the recovered arithmetic summation order
  (`0x408447..0x40849b`), and the compact sample association retains the
  observed offset between leveled values, time and geometry (geometry from
  sample current+1, leveled values starting at arc_start+2);
- a per-epoch satellite eligibility mask is precomputed exactly like the
  binary's preliminary check (boundaries included there, excluded in the
  equation accumulation).

### 4.5 Unconstrained solver — `solver.py:fit_prepared`

[`fit_prepared`](N:/ict-hub/absoltec_native/solver.py:276) is the bounded-
memory form of the daily weighted least-squares:

1. epoch grid `0, step, …, 24` (the 24 h epoch is an internal fit window and
   is dropped from the output);
2. per window: strict ±1 h selection, the Eq. 6 weight, the seven-column
   design `[1, dlon, dlat, dlon², dlat², dt, dt²]`, column scaling, and a
   rank/condition screen (QR + singular values against `condition_limit`);
3. local coefficients are QR-eliminated per window; only the satellite-sized
   Schur complement is accumulated:

```text
gram = sum over windows [diag(sum w_b^2) - qtz^T qtz]
rhs  = sum over windows [bincount(w_b * weighted_tec) - qtz^T qtb]
```

   where `qtz = Q^T·(one-hot·w_b)`, `w_b = sw/S`. Storage is
   O(K·7·N_sat + N_sat²) — never a day-sized dense matrix;
4. the Schur system is scaled, symmetrized and eigen-checked
   (`eigvalsh`, ratio against `condition_limit`): an unidentifiable bias
   system fails loudly instead of publishing biased output;
5. combined biases are solved, then each window's seven coefficients are
   recovered by triangular back substitution.

### 4.6 Bounded-variable solver — `solver.py:_fit_bvls` (`--nonneg`)

[`_fit_bvls`](N:/ict-hub/absoltec_native/solver.py:384) solves the same
consistent 24 h system with the Eq. 8 bounds:

1. identical screening, windows, weights and column scaling;
2. the full system is assembled **sparsely**: one block of seven scaled local
   columns per kept window plus one scaled one-hot bias column per
   observation (`bias column = (sw/S)/bias_scale_j`), `bias_scale_j` being the
   column norm, so every column is comparably scaled;
3. bounds in scaled space:

```text
lb[V_k]   = 0.5 * scale_V          (I_V >= C = 0.5 TECU)
ub[bias_j]= (min_exp_j - 0.5) * bias_scale_j
```

   where `min_exp_j` is the smallest experimental slant TEC of satellite j —
   this keeps slant TEC above C along every used line of sight;
4. `scipy.optimize.lsq_linear` with method `bvls` (Start–Parker, as in the
   paper) when the dense conversion fits a 5·10⁶-element budget, otherwise
   the sparse-capable `trf` algorithm for the same bounded problem;
5. coefficients are rescaled back per window; the diagnostics record the
   solver used, the number of active bounds, and the same screening
   information as the unconstrained path.

`--model legacy-2017` rejects `--nonneg`, `--auto-alpha` and a non-default
`--shell-height-km`: the binary's literals and unconstrained solve are part of
what is being reproduced.

### 4.7 Bias reporting — `bias.py`

[`split_biases`](N:/ict-hub/absoltec_native/bias.py:11) splits combined link
biases after the fit: per-constellation zero mean for satellites, the
remainder as one receiver term per constellation. The DCB DAT is
legacy-shaped, and the JSON description states these are combined link
biases, not calibrated hardware DCBs.

### 4.8 Batch orchestration — `__main__.py`

[`_run_station`](N:/ict-hub/absoltec_native/__main__.py:116) is one
station-day transaction:

1. exclusive `.SITE.lock`, input identity (SHA-256 + size, re-checked after
   reading so a file changing mid-run aborts the task);
2. unsupported constellations are excluded and counted (only G*/R* fitted);
3. solve, then stage the four outputs in a temp directory and publish the
   whole folder atomically (`os.replace`); with `--overwrite` the previous
   folder is renamed first and restored if the publish fails;
4. failures keep a JSON record under `_failures/YYYY/DDD/` and never replace
   a previous good result.

The CLI ([parser](N:/ict-hub/absoltec_native/__main__.py:305),
[main](N:/ict-hub/absoltec_native/__main__.py:345)) discovers
`YYYY/DDD/SITE` trees, runs stations in a thread pool (`--jobs`), and returns
a failing exit code when any station is partial or failed.

## 5. Updates applied on 2026-11-17/18 (TuRBOTEC science upgrades)

Based on the literature review, three changes were implemented in the
`--model published` path. The legacy path is unchanged.

| # | Update | Flag / default | Where |
|---|---|---|---|
| 1 | 1/S elevation-weighted leveling (Eq. for `const`, stage 4) | default on for published | [`solver.py:_prepare_modern`](N:/ict-hub/absoltec_native/solver.py:121) |
| 2 | Latitude-dependent α 0.87/0.94/0.97 (TuRBOTEC Table 1), plus overridable shell height so Schaer's canonical MSLM (0.9782, 506.7 km) is reproducible | `--auto-alpha` (off), `--shell-height-km` (450) | [`geometry.py:turbotec_alpha`](N:/ict-hub/absoltec_native/geometry.py:91), [`solver.py`](N:/ict-hub/absoltec_native/solver.py:31) |
| 3 | Bounded-variable least squares, Eq. 8 (C = 0.5 TECU) | `--nonneg` (off) | [`solver.py:_fit_bvls`](N:/ict-hub/absoltec_native/solver.py:384) |

Examples:

```powershell
# TuRBOTEC-faithful published run
python -m absoltec_native --dat-path N:\RINEX\out --output-dir N:\RINEX\abstec_native_out `
  --year 2025 --day-of-year 32 --site aksu --model published --auto-alpha --nonneg

# Schaer canonical MSLM variant
python -m absoltec_native ... --model published --correction-coefficient 0.9782 --shell-height-km 506.7
```

Verification of the upgrades (all in `tests/test_absoltec_science.py` and
`tests/test_absoltec_cli.py`):

- the leveling offset equals Σ(d/S)/Σ(1/S), computed independently;
- `turbotec_alpha` reproduces the paper's three tabulated stations;
- with a synthetic low-V day (true V ≈ 0.1–0.3 TECU) the unconstrained solve
  returns V < 0.5, the bounded solve lifts it to ≥ 0.5 with the lowest epochs
  pinned at exactly C, and every combined bias respects its
  `min(I_Exp) − 0.5` cap;
- on a normal day where bounds are inactive, `--nonneg` reproduces the
  unconstrained solution (validates the bounded machinery and the forward
  time convention);
- legacy mode rejects all three options.

On real AKSU 2025/032 data the end-to-end run with all options succeeds
(48/48 epochs, 1.1 s) and, on that quiet day, changes the output by only
~1e-10 TECU: all bounds are inactive there (I_v between 6.2 and 47 TECU),
which matches the paper's finding that the constraint bites on the ~9% of
epochs where negative TEC would otherwise appear.

## 6. Legacy-specific recovered behaviors (parity reference)

| Behavior | Evidence |
|---|---|
| Strict ±1 h windows | `0x405eae`, `0x405eb8` |
| Temporal basis `epoch − observation_time` | `0x405edb–0x405ee3` |
| Slant weight `1/[S(1+dt²)]` | normal-block/RHS accumulation in main |
| Mapping radius literal 6371302.0 m, height 450 km | `0x402f66–0x402f78`, `0x407dd6–0x407df6` |
| IPP geometry radius 6371.0 km, `asin` longitude | `0x401bf0` |
| Reader ignores the seventh (validity) column | `0x4043b8–0x404511` |
| 30-s decimation, 10° arc threshold, 124-s gap, fixed buffers, 1999-row cap | main preprocessing |
| Spike repair with 4σ / 5-TECU thresholds + phase-tail shift | main preprocessing; exact edges not fully reconstructed |
| Double→single→double rounding in solver routines | `cvtsd2ss`/`cvtss2sd` at `0x401304…`, `0x4015cd…`, `0x4018ef…`, `0x4020b7…` |
| Compact sample association offset (time/geometry/leveled values) | observed in instructions; compatibility path reconstructs it |

These quirks exist for parity only. The published path deliberately diverges:
quadrant-aware longitude, validity-aware arcs, unrestricted cadence, no input
cap, no phase-tail shifting, 6371 km/450 km consistently, and the upgrades of
section 5.

## 7. Validation status and known limits

- **Parity (legacy-2017 vs the 2017 binary):** failing at the 0.001 TECU
  absolute tolerance. On the five-day AKSU corpus (2025/032–036, 0.5 h) the
  compatibility path shows vertical-TEC RMSE ≈ 0.0098–0.0348 TECU, maximum
  difference ≈ 0.023–0.044 TECU. The remaining gap is attributed to
  unreconstructed preprocessing edge behavior and the old solver's
  single-precision rounding — not to the model, which is now confirmed
  published. The original prints only three decimals, which is a hard floor
  for strict comparisons.
- **Scientific accuracy (published):** not yet validated against external
  references. The literature places this method at ~1.5–3 TECU against
  CODE-class GIMs (TuRBOTEC itself: 1.7 TECU RMS at a mid-latitude station).
  Recommended stack: IRI echo-simulation, CODE GIM comparison, dSTEC arc
  consistency, Jason altimetry — always several independent references.
- **Usage limits (from the paper, apply to both models):** per-station fit
  valid within ~10–15° of the station (errors exceed 10 TECU beyond, faster
  in latitude); gradient coefficients substantially less accurate than the
  vertical TEC level (up to 1 TECU/deg at equatorial stations); GLONASS
  biases can differ from CODE by 10–17 TECU because FDMA inter-frequency
  biases fold into them (expected, not a bug); second-order ionospheric
  terms (~0.05 TECU per 100 TECU) are negligible here.
- **Tests:** the focused suites cover the seven coefficients against
  independent exact quadratic signals, Cartesian ray/shell geometry
  cross-checks, equatorial/polar/dateline stations, GPS+GLONASS, both code
  combinations, nonzero-mean biases, noise, slips, long ambiguities, missing
  data, one-minute grids, permutation invariance, degenerate geometry, legacy
  screening rules, batch publication, and the four upgrades of section 5.
  Full suite: **582 passed** (NumPy 1.26.4, SciPy 1.13.1).

## 8. References

1. Yasyukevich Yu.V., Mylnikova A.A., Kunitsyn V.E., Padokhin A.M., "Influence
   of GPS/GLONASS differential code biases on the determination accuracy of
   the absolute total electron content in the ionosphere", *Geomagnetism and
   Aeronomy*, 55(6), 2015, DOI 10.1134/S001679321506016X (paywalled; primary
   algorithm reference).
2. Yasyukevich Yu.V., Mylnikova A.A., Polyakova A.S., "Estimating the total
   electron content absolute value from the GPS/GLONASS data", *Results in
   Physics*, 5:32–33, 2015, DOI 10.1016/j.rinp.2014.12.006.
3. Yasyukevich Yu.V., Mylnikova A.A., Vesnin A.M., "GNSS-Based Non-Negative
   Absolute Ionosphere Total Electron Content, its Spatial Gradients, Time
   Derivatives and Differential Code Biases: Bounded-Variable Least-Squares
   and Taylor Series", *Sensors*, 20(19):5702, 2020,
   DOI 10.3390/s20195702 (open access; the reconstruction's reference spec:
   Eqs. 3, 4, 6, 8, 9 and stage 4 leveling).
4. Schaer S., "Mapping and Predicting the Earth's Ionosphere Using the Global
   Positioning System", PhD thesis, University of Berne, 1999 (MSLM,
   α = 0.9782, H = 506.7 km canonical fit, via Gulyaeva 2021).
5. Mannucci A.J. et al. 1998; Ciraolo L. et al. 2007 (phase-code leveling);
   Blewitt G. 1990 (cycle slips); Klobuchar J. 1987 (single-layer mapping) —
   full bibliographic details in the science review report.
