# Native AbsolTEC reconstruction

This is an **experimental, runnable scientific reconstruction**, not yet a
validated drop-in replacement for TayAbsTEC. It addresses the old executable's
memory architecture and Windows/Wine dependency. It does not patch or invoke
`absolTEC.exe`, use a reference curve to calibrate its answers, or change the
hub's current runner. Numeric agreement with the original is currently **failing**.

The complete description and implementation reference, including the TuRBOTEC
science upgrades applied on 2026-11-17/18, is
[absoltec_full_description.md](absoltec_full_description.md).

## Run separately from existing output

Python 3.11 or 3.12 is supported; the container uses 3.12.

```powershell
python -m pip install -r requirements-absoltec-native.txt
python -m absoltec_native --dat-path N:\RINEX\out --output-dir N:\RINEX\abstec_native_out --year 2025 --day-of-year 32 --site aksu --time-step-hours 0.016666666666666666
```

Inputs follow `ROOT/YYYY/DDD/SITE/*.dat`. Use a new output root to keep legacy
results available for comparison. `--days 032-034` and `--site aksu,mork` select
batches; `--jobs` permits independent station jobs. `--help` lists all options.
The published model additionally accepts the TuRBOTEC science options
`--auto-alpha`, `--shell-height-km` and `--nonneg` (science review below); the
legacy model rejects them to preserve parity with the 2017 binary.
Writes are staged per station, with input hashes, parameters, and diagnostics.
Existing output requires explicit `--overwrite`. Failed jobs retain their failure
diagnostics and do not replace successful old results. Partial output has NaN
gaps and a failing batch exit status rather than invented zero measurements.

```sh
docker build -f Dockerfile.absoltec-native -t abstec-native:experimental .
docker run --rm -v /path/to/tec-dat:/data/in:ro -v /path/to/new-output:/data/out abstec-native:experimental --dat-path /data/in --output-dir /data/out --year 2025 --day-of-year 32 --site aksu --time-step-hours 0.016666666666666666
```

The Docker recipe has not been built in this session: the local Docker engine
is unavailable. The Python entry point is exercised directly on Windows.

Outputs use `ROOT/YYYY/DDD/SITE/`: an eight-column TEC DAT file, DCB DAT file,
combined-bias JSON, and `diagnostics.json`. DCB separation uses a zero mean
within each constellation **after** the combined link biases are fitted. The
receiver term is a reporting convention, not an externally calibrated DCB.

## What was recovered with the requested tools

[awesome-reverse-engineering](https://github.com/morluto/awesome-reverse-engineering)
is a catalogue. Its featured [REA](https://github.com/morluto/rea) was used as
version 6.9.0 with Ghidra 12.2 and a portable JDK 25. Four application functions
were exported successfully, excluding 781 other procedures, primarily runtime
code. Source bytes were not changed by analysis.

Target: 843,776-byte x86 PE, linker timestamp 2017-04-24; SHA-256:
`31984eb0df935b52751cf92458b4471ca8260a98fed9d5fb6492fe175d3443f5`.

| Finding | Evidence / confidence |
| --- | --- |
| Static `.data` virtual size 1,372,271,116 bytes in a 32-bit image | PE section headers, observed. Consistent with the earlier memory failures; not proof of every crash cause. |
| Dense inverse plus matrix-vector solve | Functions `0x401000`, `0x401d80`; caller `0x402cd0`. |
| Repeated double-to-single-to-double rounding inside those routines | `cvtsd2ss`/`cvtss2sd` at `0x401304–0x401308`, `0x4015cd–0x4015d1`, `0x4018ef–0x4018f3`, `0x4020b7–0x4020bb`, among others; observed instructions. The new solver retains double precision. |
| Daily fit has seven local coefficients plus shared satellite link biases | Main function's normal-system construction. |
| Strict +/-1-hour windows | Instructions `0x405eae`, `0x405eb8`. |
| Temporal basis uses `epoch - observation_time` | Instructions `0x405edb–0x405ee3`. |
| Slant-model weight `1/[S*(1+dt^2)]` | Normal-block, RHS, and bias-diagonal accumulation in main; recovered algebra. |
| Mapping uses radius 6371302.0 with height 450.0 | Instructions `0x402f66–0x402f78`, `0x407dd6–0x407df6`; both constants independently decoded from bytes. |
| IPP geometry uses radius 6371.0 with height 450.0 | Function `0x401bf0`; incompatible length scales with the mapping expression above. |
| IPP longitude uses asin, losing the polar quadrant | Function `0x401bf0`; improved atan2 is available in the published model. |
| Reader consumes six values, ignoring the seventh validity bitmask | Inspected reader `0x4043b8–0x404511`. |
| Hardcoded 30-second decimation, 10-degree arc threshold, 124-second gap and fixed sample buffers | Main preprocessing; configurable cutoff is applied later. |
| Repairs spikes and shifts subsequent phase samples using 4-sigma / 5-TECU thresholds | Main preprocessing. Exact edge behavior is not reconstructed. |

An offset between the compact leveled values, time, and geometry was also found
in the instructions. The compatibility path reconstructs this association,
including the omitted initial samples, and records which preprocessing path ran.
Its exact edge behavior is not fully validated. The published path keeps each
observation's measurements and geometry together.

REA import evidence ID:
`ev_bd229a1a5619d1bad8aa183a6da7afdf455587a19bdf214bcc1641c3ad202895`.
The decompilation's analysis-profile digest is
`cd0725271a2a9827d9633995040408738e90d0edfb3e49983df18bf70a146842`.
Full evidence and pseudocode are retained locally at
`C:\Users\VV\AppData\Local\AbsolTEC-Reconstruction\`.
`scripts/reverse_absoltec.ps1` repeats the focused export using caller-selected
REA, Ghidra and JDK installations. It does not register MCP servers or change
agent configuration. Decompiled C is approximate pseudocode, not original source;
SSE math-call expressions were checked against instructions where necessary.

## Implementation and deliberate differences

The fitted model is:

```text
slant_TEC = S * [V + Glon*dlon + Glat*dlat + Qlon*dlon^2
                  + Qlat*dlat^2 + Gt*dt + Qt*dt^2] + B_satellite
```

The eight DAT columns retain `UT, I_v, G_lon, G_lat, G_q_lon, G_q_lat, G_t,
G_q_t`. Quadratic values are polynomial coefficients, without a hidden 1/2
factor. Their dimensional units are TECU/degree^2 and TECU/hour^2.

- `--model legacy-2017` retains the recovered mapping radius, asin longitude,
  backward temporal convention, scalar spike/slip repairs, compact sample
  association, and satellite eligibility per output window. It intentionally
  reproduces zero-placeholder handling, ignored validity flags, 30-second
  selection, and the 1999-sample input cap. This compatibility path is still
  approximate; the name does not certify legacy numerical parity.
- `--model published` uses a consistent 6371-km Earth radius / 450-km shell,
  quadrant-aware longitude, and forward time. This is an explicitly different
  scientific model, not claimed to reproduce the 2017 executable.
- Both use strict time windows and recovered weighting, and daily combined
  bias estimation. Leveling differs: the published path levels each arc with
  a 1/S elevation-weighted phase-to-code mean (TuRBOTEC stage 4), while the
  legacy path keeps the recovered arithmetic recurrence. No GPS zero-mean
  constraint is imposed on combined biases.
- In the published path, missing code, missing phase, and cycle slips are
  interpreted using the actual tec-suite bitmask. Valid zero measurements remain
  valid, arcs split on flags/gaps, sampling cadence is unrestricted, and the
  legacy spike-overwrite/phase-tail-shift heuristics and input cap are removed.
- GPS/GLONASS are supported; unsupported constellation files are excluded and
  counted. Neither path retains the old 70-TECU output ceiling. Adjacent-day
  context is not yet loaded; duplicate satellite files are rejected rather than
  silently mixed. Daily output grids use dynamically sized storage in both paths.
- Insufficient or ill-conditioned windows become NaN gaps; an unidentified daily
  bias system fails with an explanation. Explicit observation/epoch budgets bound
  allocation. No dense day-sized matrix or row-by-row Python design is built.

The solver column-scales and QR-eliminates each window's seven coefficients,
accumulates a satellite-sized Schur system, then recovers the local coefficients.
Storage grows with observations, windows, and satellites, rather than the square
of all daily parameters. The reduced normal system still requires conditioning
checks; those values are recorded. It has been compared with an independently
constructed noisy dense weighted least-squares solution.

## Science review: TuRBOTEC verification and applied improvements (2026-11-17)

A literature review ([AbsolTEC TEC mapping science review](reports/AbsolTEC%20TEC%20mapping%20science%20review.md))
verified all three cited papers and established that the recovered algorithm is
the published Irkutsk-group single-station method. The open-access successor
paper — Yasyukevich, Mylnikova & Vesnin, "GNSS-Based Non-Negative Absolute
Ionosphere Total Electron Content...", *Sensors* 20(19):5702, 2020,
DOI 10.3390/s20195702 (TuRBOTEC) — contains the reconstructed model almost
verbatim: Eq. 3 (seven coefficients plus combined link biases, mixed
derivatives neglected), Eq. 4 (6371 km / 450 km / α = 0.97 at mid-latitudes),
Eq. 6 (1/[S·(1+dt²)] slant weighting over ±1 h windows), Eq. 9 (post-fit
zero-mean bias gauge per constellation "following Schaer") and stage 4 leveling.

Three TuRBOTEC improvements are implemented in the `--model published` path:

- **1/S elevation-weighted leveling (default).** Arc offsets are the
  elevation-weighted mean Σ(d/S)/Σ(1/S) of code-minus-phase differences
  (TuRBOTEC stage 4). The printed paper formula carries a stray factor; the
  dimensionally consistent weighted mean is implemented. The legacy path keeps
  the recovered arithmetic recurrence.
- **Latitude-dependent α (`--auto-alpha`).** Picks the MSLM correcting
  coefficient 0.87 (|lat| < 25°), 0.97 (25–65°) or 0.94 (> 65°) following
  TuRBOTEC Table 1. The paper tabulates three stations rather than band
  boundaries; these thresholds place its stations inside their bands.
  `--shell-height-km` overrides the 450 km shell, so Schaer's canonical MSLM
  is reproducible as `--correction-coefficient 0.9782 --shell-height-km 506.7`.
- **Bounded-variable least squares (`--nonneg`).** Solves the consistent 24 h
  system with TuRBOTEC Eq. 8 bounds: every vertical TEC estimate is at least
  C = 0.5 TECU and every combined bias stays below the smallest experimental
  slant TEC of its satellite minus C. Uses `scipy.optimize.lsq_linear`
  (Start–Parker bvls when the dense matrix fits the resource budget, sparse
  TRF otherwise). In the paper's own comparison about 9% of epochs change by
  more than 1 TECU relative to the unconstrained solve.

Usage limits the authors quantified and that apply to this implementation's
outputs as well: the per-station fit is valid within roughly 10–15° of the
station (errors exceed 10 TECU beyond, faster in latitude), the gradient
coefficients are substantially less accurate than the vertical TEC level (up
to 1 TECU/deg at equatorial stations), and per-station GLONASS biases can
differ from CODE DCBs by 10–17 TECU because FDMA inter-frequency biases fold
into them — expected, not a bug. Second-order ionospheric terms
(~0.05 TECU per 100 TECU) are negligible here. For physical validation the
paper's own stack is recommended: IRI echo-simulation, CODE GIM comparison
(~1.7 TECU at mid-latitudes), dSTEC arc consistency and Jason altimetry,
always against several independent references.

## Validation status

The focused tests cover independent exact quadratic signals, all seven coefficients,
nonzero-mean combined biases, multiple grids down to one minute, a noisy dense
oracle, rank loss, NaN gaps, validity flags, slips, missing code, file validation,
recovered legacy screening rules, batch publication, the 1/S weighted leveling
formula, the TuRBOTEC latitude-dependent mapping coefficient, and the
bounded-variable non-negative solve (clamping, inactive-bound equivalence, and
slant-TEC-preserving bias caps). They establish the
implemented model, not legacy parity. Tests pass with NumPy 1.26.4 and SciPy
1.13.1, matching the standalone dependency pins.

The expanded suite includes 23 independent science cases in
`tests/test_absoltec_science.py`: Cartesian ray/shell geometry, equatorial and
polar stations, the dateline, GPS and GLONASS combinations, both code-observation
formats, nonzero-mean biases, noise, cycle slips, long phase ambiguities, missing
data, one-minute output, permutation invariance, and degenerate geometry. Expected
signals and geometry are generated independently of the production solver. The
21 validation-harness tests separately check missing evidence, incomplete or
zero-filled references, coefficient and DCB mismatches, subprocess failures, and
short relative input paths for the original executable.

A local AKSU station-day (2025/032) was generated from an existing RINEX archive
with official tec-suite 0.7.8. That release required relabeling isolated copies'
RINEX version header from 3.04 to 3.03. Original archives were unchanged; hashes
and the adaptation are in `real-fixture/rinex303-provenance.json` in the analysis
cache. This is a conditional interoperability fixture, not a certified conversion.
The original executable successfully produced a real 48-epoch reference from its
54 GPS/GLONASS DAT files. The native solver produces complete 48-, 480-, and
1440-epoch outputs, but its numeric comparison with that reference **fails** at
an absolute tolerance of 0.001. The final recovered compatibility path has a
vertical-TEC RMSE of approximately 0.0126 TECU and maximum difference of 0.0233
TECU on that one case; this is not a station-independent accuracy guarantee.
Remaining preprocessing edge behavior and the old solver's precision loss have
not been separated as causes. A separate clean
synthetic test also shows differences between the old executable and the known
signal. No offset is fitted to make comparisons look better. The current machine-
readable historical record is [absoltec_validation.json](absoltec_validation.json); it includes
the per-column comparison, complete output counts, timing scope, and local output
paths. These runtimes are observations on one computer, not a performance claim
against the old executable.

On 2026-11-17, a fresh five-day AKSU corpus (2025/032-036) was tested with both
models at 0.5-hour, 0.05-hour, and one-minute spacing. The original executable ran
in isolated directories at 0.5-hour spacing. Four days produced complete native
and original grids; the sparse day 034 produced partial results in both engines
and was correctly rejected by the coverage gate. The compatibility mode still
fails the 0.001 absolute tolerance on every complete day; all seven coefficients
and DCB keys are checked. The intentionally different published model also fails
legacy agreement. No fine-grid legacy agreement or external physical accuracy
is claimed. All 92 numerical tests invoked by the validation harness passed.

The current [science validation report](absoltec_science_validation.json),
[run manifest](absoltec_science_manifest.json), and
[input provenance](absoltec_input_provenance.json) preserve these results.
[Evidence archive](absoltec_science_evidence.zip) contains outputs, per-case
manifests, execution logs, and numerical test evidence. Raw input files and the
original executable remain in the local analysis cache; their hashes are
recorded. The five-day corpus uses the same documented RINEX header adaptation
and is not a certified input conversion. Adjacent-day observations were excluded
from both engines, so this does not test legacy neighboring-day behavior.

```sh
python -m pytest tests/test_absoltec_bias.py tests/test_absoltec_cli.py tests/test_absoltec_geometry.py tests/test_absoltec_io.py tests/test_absoltec_legacy.py tests/test_absoltec_solver.py tests/test_absoltec_science.py tests/test_compare_absoltec.py tests/test_validate_absoltec_science.py
python scripts/compare_absoltec.py original.dat native.dat --json --atol 0.001
python scripts/validate_absoltec_science.py --dat-path /path/to/dat --legacy-exe /path/to/absolTEC.exe --output-dir /path/to/new-validation --year 2025 --days 032-036 --sites aksu --run-numerical-tests --input-provenance /path/to/provenance.json
```

The comparison aligns UT without interpolation, checks all seven columns and
coverage, and returns exit code 1 on mismatch. Full empty or missing output does
not count as a pass. The original prints only three decimals, which places a
rounding floor on strict comparisons.

Before switching the hub's runner, validation still needs representative paired
GPS/GLONASS station-days, adjacent-day boundaries, sparse inputs, disturbed
conditions, and agreed scientific tolerances. The deployed runner is unchanged.

## Hub runner audit, 2026-11-17

The logged-in hub at `http://10.0.0.130:8081/` was inspected read-only. Job 76
failed with XP guest exit code 9020 and repeated "The system cannot execute the
specified program." Job 71 displayed SUCCESS despite final unrecovered station
failures. [Saved audit](absoltec_hub_audit.json) records the job links and logs.
The exact XP failure cause cannot be established from these logs alone.

### Native runner integration, 2026-11-18

The native reconstruction is now wired into the hub as a separate converter,
`abstec-native` ("AbsTEC Native"), built from `Dockerfile.absoltec-native`:

- `app/registry.py` — converter entry: image, `/data/in` + `/data/out`
  volumes, `--year/--day-of-year/--days/--site`, `--model` select
  (legacy-2017 / published), `--time-step-hours`, `--elevation-cutoff`,
  `--correction-coefficient`, `--window-hours`, `--auto-alpha`,
  `--shell-height-km`, `--nonneg`, `--jobs`, `--overwrite`, and progress
  patterns for the live log bar.
- `app/converters.py` — page context (same tec-suite DAT pickers as the
  legacy AbsTEC form) and server-side form validation: year/day selection,
  model choice, numeric bounds, and the rule that `--auto-alpha`,
  `--nonneg` and a custom shell height require the published model.
- `app/templates/run.html` — the shared year/day/site picker section now
  renders for both AbsTEC converters.
- `docker-compose.yml` + `app/config.py` — `ABSTEC_NATIVE_IMAGE`
  (local tag, no Docker Hub repository yet), `ABSTEC_NATIVE_OUTPUT_DATA_PATH_HOST`
  and container mount `/mnt/abstec-native-out`.

It does not replace the legacy `abstec-suite` converter or its dockur XP
runner; both remain available. Locally verified on 2026/001: a published
model run with `--auto-alpha --nonneg` published a partial station-day with
diagnostics (data gap, 19/48 epochs), the overwrite guard refused a second
unflagged run, and a legacy-2017 run solved a full station-day (48/48).

Local hub changes now mark final unrecovered AbsTEC station failures as failed
even when the wrapper exits zero, retain a coded failure event despite log
pruning, and inspect retained logs on reattachment. Failed cleaned attempts that
succeed on raw input and legitimate existing-output skips remain distinct.
Docker client initialization failures now terminate log streaming, and runner
parameters are validated before submission. These changes are not deployed.

Primary sources: [2015 model paper](https://doi.org/10.1134/S001679321506016X),
[2015 short method paper](https://doi.org/10.1016/j.rinp.2014.12.006),
[tec-suite DAT writer](https://github.com/gnss-lab/tec-suite/blob/18465f906a3176a2eaf130e872bfd4b650dc2721/tecs/dio/text.py),
[validity bits](https://github.com/gnss-lab/tec-suite/blob/18465f906a3176a2eaf130e872bfd4b650dc2721/tecs/validity.py).
The [2020 TuRBOTEC paper](https://www.mdpi.com/1424-8220/20/19/5702) is a successor,
not evidence that every newer detail existed in this binary.
