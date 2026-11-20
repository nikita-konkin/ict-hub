# MapTEC 2014 reconstruction

MapTEC Native turns tec-suite DAT observations into scattered maps of filtered
TEC variations at ionospheric pierce points. The reference is the ISTP SB RAS
32-bit x86 PE linked on 2014-10-24 with SHA-256
`3da31ec65c09c97c25539b3722842406a9fb09afecd43fad4448f5c080e29343`.
The scientific context is the 300 km thin shell, short smoothing window, and
roughly 20-minute detrending used by the same research group.
[Afraimovich et al. (2009)](https://doi.org/10.5194/angeo-27-1521-2009)

## Evidence

REA 6.9.0 with Ghidra 12.2 exported the PE import table and ten application
helpers. Ghidra's 50 MiB native decompiler response limit prevented a
pseudocode export of the large main procedure at `0x405e46`. REA exported
26,287 main-procedure instructions. A Capstone listing and scratch executions
were used to corroborate the call sites and behavior. Addresses are image
virtual addresses for the hashed build. The 2013 `MapTEC.dia` has different
settings and paths. The 2016 AutoIt GUI edits `DT.dia` and launches
`MapTec.exe` from its own directory.

| Finding | Addresses | Confidence |
| --- | --- | --- |
| Main opens `DT.dia`. The parser reads the layout switch and fixed format, then dispatches current-day records through the header-based or fixed-layout branch. Group 4 selects a seven-field formatted read. | `0x40602c`, `0x406be5–0x406d75`, `0x40e482`, `0x40f033–0x40f07d`, `0x410203` | High |
| Arc processing converts both window durations into sample counts. The smoother makes even windows odd by subtracting one and contracts them symmetrically at the edges. The recovered trend helper calls this smoother and subtracts the resulting long mean. The main calls the short smoother and trend helper separately. | `0x4244c5–0x424523`, `0x424c62`, `0x42580d`, `0x402b70`, `0x401538` | High |
| The main subtracts an arc baseline and adds the configured minimum divided by an incline factor, then conditionally multiplies by an epoch factor. The pierce point uses a 6372 km sphere. The main uses a separate factor helper at `0x405d8c`. | `0x42411b`, `0x4241d5–0x424233`, `0x4243b5`, `0x424488`, `0x4270b6`, `0x4024cc` | High for the operations, medium for the full arc baseline selection |
| The output time is the configured start plus an index divided by 120 epochs per hour. The per-arc geometry lookup adds a literal 12 samples and the skipped-edge count to its internal index. The synthetic probe found a 40-sample observation discrepancy for that fixture. | `0x427df7–0x427e67`, `0x426fb7–0x4270b6` | High for the time grid and observed discrepancy, medium for the source of the total offset |
| The absolute dI threshold gates row writes. The formatted row includes UT, pierce latitude, pierce longitude, dI, smoothed TEC, azimuth, elevation, receiver latitude, receiver longitude, site, and satellite filename. The map loop calls the gnuplot helper for PNG generation. | `0x4273d8–0x427456`, `0x42745e–0x427b54`, `0x428ae3`, `0x403af4` | High |

To reproduce, run the PowerShell script with the original path, the portable
REA `package/scripts/rea.mjs` entry, the Ghidra and JDK roots, and a new
output directory on local NTFS. The script checks the executable hash before
copying it. The completed evidence and scratch results are in
`%LOCALAPPDATA%\MapTEC-Reconstruction`. The executable, input copies and
disassembly are kept outside this repository.

## Why the archived maps were empty

The archived `Out` contains 2,881 zero-byte `.dat` files covering UT 0–24 and a
1,204-byte Windows shortcut. The shipped `DT.dia` selects fixed format and
group 4, which expects seven fields in positions defined by its Fortran
descriptor. The supplied DATs start with six-character TSN and eight-character
UT fields, followed by a 19-character datetime, angles, raw observables,
`tec.p2c1`, `tec.l` and validity. The first configured 11-character integer
and the following 14-character float already cross actual field boundaries.

All runs used copies of the 220 supplied DATs, four sites and the same four
map epochs from UT 1.00000 through 1.02500.

| Scratch treatment | Populated maps | Rows |
| --- | ---: | ---: |
| Shipped parser settings, with adjacent paths pointed to the scratch input root | 0 of 4 | 0 |
| Header-based parser, original `tec.l1l2` parameter | 0 of 4 | 0 |
| Header-based parser, `tec.l` parameter | 0 of 4 | 0 |
| Repack rows to the configured fixed descriptor and group 4, original `tec.l1l2` parameter | 4 of 4 | 136 |
| Same repack, explicitly select `tec.l` | 4 of 4 | 136 |
| Same repack with copied day 119 and 121 surrogate inputs, explicitly select `tec.l` | 4 of 4 | 146 |

The two repaired fixed-format runs without adjacent days had byte-identical
DATs. Repacking puts the existing `tec.l` numeric value in field 5 and the
existing code TEC in field 6. The evidence isolates the fixed-layout mismatch
as sufficient to explain the archived empty run. Group 4 works when the records
match its layout. The original neighbour directories were absent, and the
repair produced rows with neighbours still absent. The legacy header-based
mode also failed on this input variant. Its separate failure was not isolated.

## Validation and parity

The native run read the unrepacked archives with `--tec-parameter tec.l`. A
second corpus independently generated 167 consecutive samples carrying a
linear trend, sinusoidal disturbance and varying elevation and azimuth. The
synthetic corpus was run in slant and vertical mode. Comparisons joined on
map filename and input satellite filename, requiring exact printed UT,
0.02° tolerance in angular fields and 0.005 TECU in dI and smoothed TEC.

| Corpus | Station-days | Epochs | Legacy / native / matched rows | Max latitude / longitude error | Max / RMS dI error | Rows within all tolerances |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Repacked archive, vertical | 4 | 4 | 136 / 193 / 101 | 1.21° / 1.99° | 0.162 / 0.0446 TECU | 0 |
| Synthetic, slant | 1 | 14 | 14 / 14 / 14 | 0.84° / 1.05° | 0.992 / 0.7193 TECU | 0 |
| Synthetic, vertical | 1 | 14 | 14 / 14 / 14 | 0.84° / 1.05° | 0.880 / 0.6222 TECU | 0 |

Numerical parity was not reached. At synthetic UT 1.00000, the legacy row's
44.00° elevation and 62.00° azimuth identify the observation at UT 0.66667.
That 40-sample offset persisted throughout the tested window. Native rows use
the observation time for both the filter and position. The archive's matched
rows agree on the printed UT and receiver position. The native PNG was also
rendered with the archived contour and computed terminator.

Python 3.12 verification passed 620 repository tests, Ruff 0.16.9 lint and
format checks, and mypy 2.3.1 for both the hub and `maptec_native`. The local
Linux Docker image produced four DAT/PNG pairs and 193 points in a
network-disabled run with the read-only archive fixture. Compose validation
passed with the MapTEC output variable unset and the other required settings
supplied. An authenticated hub page smoke test rendered the selectors and
MapTEC settings with a stubbed indexer.

## Native behavior

- The header defines the time, geometry and selected TEC fields. The selected
  signal's missing-observation flags remove samples and applicable loss-of-lock
  flags start a new arc. [tec-suite validity definitions](https://github.com/gnss-lab/tec-suite/blob/18465f906a3176a2eaf130e872bfd4b650dc2721/tecs/validity.py)
- Bounded gaps within an arc are interpolated for filtering. Output uses
  observed samples only. Edge samples are discarded before filtering and
  the two means contract symmetrically near the retained arc edges.
- The map grid is anchored at midnight. The converter uses full-precision
  trigonometry, and the longitude calculation preserves the correct quadrant
  at the poles and dateline.
- Adjacent days are optional, use the input root by default, and may be supplied
  at separate roots via the CLI. Missing or incompatible tracks are counted
  in the diagnostics. A completely empty selection exits with a failure.
- Each populated epoch gets a legacy-column DAT and a matplotlib PNG.
  Epochs without retained points are omitted and counted in the day report.
  One staged directory per day aggregates all selected stations. Use
  `--overwrite` to replace a completed day. A failed attempt produces a
  separate diagnostic report.
- The optional world contour is read from a local file. The optional solar
  terminator is Earth's geometric shadow boundary at shell height using an
  approximate solar position. [NOAA solar position equations](https://gml.noaa.gov/grad/solcalc/solareqns.PDF)

## Remaining verification

The reference does not establish general legacy epoch indexing, complete
legacy arc-gap and splice rules, or the old header-based parser's behavior on
other tec-suite variants. Real adjacent-day continuity and the other TEC
combinations have no paired legacy corpus on this machine. The terminator
could not be compared with the external ST_line output. The live hub-to-Docker
job workflow was not exercised against a deployed hub.
