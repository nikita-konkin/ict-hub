# AbsolTEC check — 17 November 2026

**Result:** software and controlled science tests pass. Real-data scientific
acceptance still fails; the native replacement remains experimental.

- **Hub:** reviewed the logged-in server at `http://10.0.0.130:8081/`.
  Job 76 repeatedly failed with XP guest exit 9020. Job 71 displayed SUCCESS
  despite unrecovered station failures. Local fixes now preserve those failures,
  prevent false batch success, validate runner settings, and end log streaming
  when Docker initialization fails. The exact XP failure cause remains unresolved.
- **Checks:** all **574 Python tests** and **7 JavaScript tests** passed.
  Lint, formatting, type checks, and whitespace checks passed. The suite includes
  23 new independent science cases covering geometry, all seven coefficients,
  GPS/GLONASS biases, noise, slips, gaps, and one-minute output.
- **Real data:** fresh comparisons covered AKSU, 2025 days 032–036, both native
  models, and three output grids. Four days had complete grids; sparse day 034
  had gaps in both engines. Strict agreement with the original at 0.001 absolute
  tolerance **failed**. The published model intentionally differs from the original.
  The harness correctly rejected incomplete results and retained the evidence.
- **Limits:** input copies required a documented RINEX 3.04-to-3.03 header
  adaptation, not a certified conversion. External physical accuracy, additional
  stations, neighboring-day behavior, and production performance remain unverified.
  Hub fixes and the native implementation are local and have not been deployed;
  the native command still needs a hub adapter. Docker image testing was unavailable.

Details: [science results](docs/absoltec_science_validation.json),
[evidence archive](docs/absoltec_science_evidence.zip),
[full test results](docs/absoltec_project_tests.xml),
[hub audit](docs/absoltec_hub_audit.json), and
[implementation notes](docs/absoltec_reconstruction.md).

Next work: resolve legacy numerical differences, validate representative data
against independent references, and integrate/test the native hub runner.

Saved in `N:\ict-hub` for the active Nextcloud Projects sync before PC shutdown.
