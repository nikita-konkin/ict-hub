# ConverterHub

A lightweight, luxury-minimal web interface for running Docker-based data converters on your local network. Built with FastAPI + HTMX for real-time log streaming without a heavy JavaScript framework.

Includes a dedicated **data-indexer microservice** that provides fast XML-indexed browsing of RINEX, TEC-suite, AbsTEC, and Parquet data structures with persistent caching.

---

## Features

- **Real-time log streaming** via Server-Sent Events (SSE) — no polling
- **Progress bar** parsed automatically from container log output
- **Session-based authentication** with bcrypt-hashed passwords
- **Role system**: `admin` (full access) and `operator` (own jobs only)
- **Audit log** of every job: who ran what, when, with which flags, and the exit code
- **Extensible converter registry** — adding a new converter is a single dict entry
- **Data indexer microservice** — fast XML-indexed browsing of data structures with:
  - **Persistent SQLite caching** — cache survives container restarts
  - **Configurable cache TTL** — balance freshness vs. performance
  - **Background refresh** — optional async/sync indexing on startup
  - **Debug logging** — detailed real-time visibility into indexing operations
- **IonMaps** — regional ionosphere maps (VTEC, |∇VTEC|, GDD, B_k) built from TEC-suite
  parquet output, with animation export, kriging interpolation and built-in accuracy
  validation (see [IonMaps methodology](#ionmaps-methodology) below)
- **Fully Dockerised** — one `docker-compose up --build` and everything runs

---

## Quick start

### 1. Build and start

```bash
git clone <this repo>
cd converter-hub

# Copy and customise environment variables
cp .env.example .env
# → At minimum, change SECRET_KEY to a random 32-character string

docker-compose up --build -d
```

Note: keep the SQLite database in the `/app/data` named volume (default). Avoid setting `DATABASE_URL=sqlite:///./dev.db`
for docker-compose runs, because that writes the DB into the container filesystem (`/app/dev.db`) and can grow very large
over long runs; use `CONVERTER_HUB_DATABASE_URL` if you need a compose-time override.

The UI is available at **http://localhost:8080** (or any LAN IP on port 8080).

### 2. First login

On first boot a default `admin` account is created with the password from the `ADMIN_PASSWORD` environment variable (default: `admin`). **Change it immediately** via the Users page.

### 3. Running a TEC-Suite job

1. Log in as `admin` (or any active user).
2. Click **TEC-Suite** on the dashboard.
3. Choose a `YYYY_original` year folder (and optional day folder).
4. Adjust parallel jobs, verbose, cleanup, and optional auto-remove (`--rm`) flag.
5. Click **Run** — the log panel appears in real time on the right side of the screen.

TEC-Suite now reads folder options from `RINEX_DATA_PATH_HOST` and passes:
- `--root /data/rinex/<YYYY_original>` when only year is selected
- `--root /data/rinex/<YYYY_original>/<DDD>` when both year and day are selected

The `--out` option is temporarily disabled and handled inside the TEC-Suite container.

---

## IonMaps methodology

The **IonMaps** section (`/ionmaps`, endpoints under `/tec-map/*`) turns TEC-suite parquet
output into regional maps of the ionosphere. The full engineering reference lives in
[`docs/tec_map_service_overview.md`](docs/tec_map_service_overview.md); this is the
method summary.

### 1. VTEC restoration (per station, per day)

1. **Input** — slant TEC time series per station+satellite link from TEC-suite [7]
   parquet output (dual-frequency phase `TEC_phase` and code `TEC_code` combinations
   [1], elevation/azimuth, receiver position from the parquet header). Samples below
   the elevation cutoff `θ_min = 20°` are dropped.
2. **Arc splitting** — each link is split into continuous phase arcs: a gap longer than
   1.5× the sampling interval (or a validity-flag break) starts a new arc. Phase TEC is
   precise but ambiguous; code TEC is absolute but noisy.
3. **Phase-to-code leveling** — every arc is shifted by
   `median(TEC_code − TEC_phase)` over the arc (arcs shorter than 3 valid samples are
   discarded): `STEC = TEC_phase + shift`. This keeps phase precision on an absolute level.
4. **Receiver bias (MSTD)** — the inter-frequency receiver bias is estimated by
   minimising the standard deviation of night-time VTEC over a bias search grid
   (a single-site technique in the spirit of the GEONET bias estimation in [6]) and
   subtracted from STEC. Satellite biases are not assimilated (no external DCB
   catalogues), so the VTEC scale is *relative*: spatial structure and dynamics are
   correct, the absolute level may carry a common offset of a few TECU.
5. **Thin-shell mapping** — STEC is projected to vertical with the single-layer model
   [2, 3] at `h_ion = 350 km` (the shell height used by the MAPGPS/Madrigal maps [4]):
   `VTEC = STEC / M(χ)`, `M(χ) = 1/√(1 − sin²χ)` where χ is the zenith angle at the
   ionospheric pierce point (IPP). IPP coordinates are computed from elevation, azimuth
   and `h_ion`; negative VTEC is clipped to 0.

### 2. Map construction (per time frame)

1. **Frame aggregation** — samples are binned into `ΔT = 15 min` frames (an order of
   magnitude finer than the 2-hour IGS GIM cadence [5], analogous to the 5-min binning
   of MAPGPS [4]); within a frame VTEC and IPP coordinates are averaged per station,
   giving one measurement point per station per frame.
2. **Spatial interpolation** onto a regular lon/lat grid (`Δ = 1°` default, bounds =
   IPP cloud + 2° margin). Three selectable principles:
   - **`linear`** (default) — Delaunay triangulation with linear interpolation inside
     the convex hull, nearest-neighbour fill outside;
   - **`kriging`** — ordinary kriging on the sphere with an exponential variogram
     `γ(h) = nugget + sill·(1 − exp(−h/range))` fitted to each frame's own empirical
     semivariogram (fallback on fit failure: range 300 km, nugget = 5% of sample
     variance). Kriging weights noisy samples through the nugget effect and relaxes to
     the field mean away from data instead of producing nearest-neighbour plateaus;
   - **`lpi`** — local polynomial interpolation: a Gaussian-weighted (σ = 200 km)
     degree-1 polynomial fitted at every grid node, with a slope ridge for degenerate
     geometries (`lpi_degree=2` switches to a local quadric where the neighbourhood
     holds ≥7 effective stations, dropping back to the plane elsewhere). Comparative
     studies rank ordinary kriging and LPI as the two most accurate local methods for
     ionosphere mapping [8].
3. **Coverage mask** — the field is physically meaningful only near measurements: grid
   cells farther than `R_cov = 300 km` (great-circle) from the nearest IPP are masked
   out. The mask is evaluated at render resolution (with 2–4× bilinear upsampling of the
   field), so the boundary is a smooth union of circles rather than pixel stair-steps.
4. **Smoothing** — Gaussian filter with `σ_g = 1` grid cell (≈100 km at Δ=1°, matching
   the published mid-latitude TEC decorrelation scale of 80–130 km [9]). Colour scale:
   5–95% quantiles of the selection, or explicit `color_min`/`color_max` for cross-frame
   comparability.

### 3. Derived propagation fields

Pointwise transforms of the VTEC grid (`N_t = VTEC·10¹⁶ el/m²`, frequencies from the
signal-band table — GPS L1/L2/L5, GLONASS L1/L2 (FDMA centre, k=0) and L3, Galileo
E1/E5a/E5b/E5, BeiDou B1I/B1C/B2a/B2I):

- **GDD** — group delay dispersion magnitude `|D| = 3·80.5·N_t / (2·c·π·f³)` [ns/GHz];
- **B_k** — coherence bandwidth `B_k = √(c·f³ / (80.5·π·N_t))` [MHz];
- **|∇VTEC|** — horizontal gradient magnitude [TECU / 100 km] with per-latitude
  correction of the longitudinal step.

### 4. Accuracy validation (LOSO)

Map quality is verified by **leave-one-station-out cross-validation**: for every frame
each station is excluded in turn, the field is predicted at its IPP from the remaining
stations with the same interpolator, and prediction errors (bias / MAE / RMSE, TECU) are
aggregated overall, per station and per frame (`GET /tec-map/validate`,
`interpolation=both` compares linear vs kriging). Reference accuracy levels for regional
networks are 0.5–1 TECU in quiet and 1.5–2 TECU in disturbed conditions [8]. Excluded
points that fall outside the coverage radius of the remaining stations are not counted.
`show_accuracy=true` prints the per-frame LOSO RMSE directly on rendered maps;
`show_params=true` prints the model constants as a caption. In practice the error budget
is dominated by residual receiver calibration, not by the interpolator — LOSO doubles as
an automatic station QC tool.

### 5. Outputs

- animation: GIF (standard/high palette) / MP4 (H.264) / WebM (VP9), `/tec-map/gif`;
- interactive Plotly snapshot, `/tec-map/snapshot`;
- publication-quality static frame PNG/SVG up to 600 dpi, `/tec-map/frame`;
- validation report JSON/CSV, `/tec-map/validate`;
- station positions + proximity grouping for the UI, `/tec-map/station-positions`.

### References

1. Афраймович Э.Л., Перевалова Н.П. *GPS-мониторинг верхней атмосферы Земли.* —
   Иркутск: ГУ НЦ РВХ ВСНЦ СО РАМН, 2006. — 480 с. (dual-frequency TEC fundamentals)
2. Mannucci A.J., Wilson B.D., Yuan D.N. et al. A global mapping technique for
   GPS-derived ionospheric total electron content measurements // *Radio Science*. 1998.
   Vol. 33, № 3. P. 565–582. (thin-shell model, mapping function)
3. Schaer S. *Mapping and predicting the Earth's ionosphere using the Global Positioning
   System.* PhD thesis. — Bern: Astronomical Institute, University of Bern, 1999. 205 p.
   (single-layer model, GIM spherical-harmonic technique)
4. Rideout W., Coster A. Automated GPS processing for global total electron content
   data // *GPS Solutions*. 2006. Vol. 10, № 3. P. 219–228. (MAPGPS / Madrigal maps:
   1°×1° binning, 5-min cadence, 350 km shell)
5. Hernández-Pajares M., Juan J.M., Sanz J. et al. The IGS VTEC maps: a reliable source
   of ionospheric information since 1998 // *Journal of Geodesy*. 2009. Vol. 83.
   P. 263–275. (IGS GIM: 2.5°×5°, 2 h, 2–8 TECU accuracy)
6. Ma X.F., Maruyama T. Derivation of TEC and estimation of instrumental biases from
   GEONET in Japan // *Annales Geophysicae*. 2003. Vol. 21, № 10. P. 2083–2093.
   (single-site receiver-bias estimation by minimising VTEC scatter)
7. tec-suite: slant TEC computation from GNSS observation files.
   https://github.com/gnss-lab/tec-suite
8. Ogryzek M., Krypiak-Gregorczyk A., Wielgosz P. Optimal geostatistical methods for
   interpolation of the ionosphere: a case study on the St Patrick's Day storm of
   2015 // *Sensors*. 2020. Vol. 20, № 10. Art. 2840. (kriging vs polynomial methods,
   cross-validation accuracy 0.5–2 TECU)
9. Shim J., Scherliess L., Schunk R.W., Thompson D.C. Spatial correlations of
   day-to-day ionospheric total electron content variability obtained from ground-based
   GPS // *Journal of Geophysical Research: Space Physics*. 2008. Vol. 113, № A9.
   A09309. (mid-latitude decorrelation length 80–130 km)

---

## Architecture overview

```
converter-hub/
├── Dockerfile                   # Python 3.12-slim image for the web service
├── docker-compose.yml           # Orchestrates converter-hub + data-indexer + volumes
├── requirements.txt
├── requirements-test.txt
├── pytest.ini
└── app/
    ├── main.py                  # FastAPI app factory, middleware, startup hook
    ├── config.py                # Settings from environment variables
    ├── database.py              # SQLAlchemy engine + session factory
    ├── models.py                # User and JobRun ORM models
    ├── auth.py                  # Login/logout routes + get_current_user dependency
    ├── jobs.py                  # Dashboard, run form, SSE stream, history
    ├── runner.py                # Docker SDK wrapper — starts/stops containers, streams logs
    ├── registry.py              # Converter registry + command builder
    ├── data_indexer_client.py   # HTTP client for data-indexer XML parsing + caching
    ├── analysis.py              # Analysis API proxy integration
    └── templates/               # Jinja2 templates (Cormorant Garamond + DM Sans)
        ├── base.html            # Sidebar layout, all CSS, HTMX scripts
        ├── login.html
        ├── dashboard.html
        ├── run.html             # Converter form page
        ├── job_panel.html       # HTMX fragment — SSE monitor panel
        ├── history.html
        └── users.html

data-indexer/                   # Separate microservice for fast data indexing
├── app.py                       # FastAPI app for indexing service
├── data_indexer.py              # Core indexing engine with persistent SQLite cache
├── Dockerfile                   # Python 3.12-slim image
├── entrypoint.sh                # Container startup script
├── requirements.txt
└── README.md                    # Detailed data-indexer configuration

tests/
├── conftest.py                  # In-memory DB, mock fixtures, authenticated clients
├── test_auth.py                 # Login, logout, session, access control
├── test_jobs.py                 # Job creation, history access control (Docker mocked)
├── test_runner.py               # Progress parsing, command building, Docker SDK mocking
├── test_data_indexer_client.py  # data-indexer client XML parsing tests
└── (8 test modules, 182 tests total)
```

---

## Adding a new converter

Open `app/registry.py` and add an entry to the `CONVERTERS` dict:

```python
CONVERTERS = {
    "tec-suite": { ... },           # existing

    "my_tool": {
        "image": "my-tool:latest",
        "label": "My Tool",
        "description": "Does something useful.",
        "container_volumes": {
            "input":  "/data/input",
            "output": "/data/output",
        },
        "progress_patterns": [r"(\d+)%"],
        "flags": [
            {
                "name": "-i",
                "long": "--input",
                "label": "Input Directory (host path)",
                "type": "text",
                "default": "",
                "required": True,
                "is_volume": "input",
                "help": "Host path to your input data.",
            },
            # ... more flags
        ],
    },
}
```

That's it — the form, command builder, and Docker invocation all adapt automatically.

---

## Data-Indexer Service

The **data-indexer** is a separate FastAPI microservice that provides fast XML-indexed browsing of RINEX, TEC-suite, AbsTEC, and Parquet data structures.

### Key Features

- **Persistent SQLite Cache** — cache survives container restarts and rebuilds
- **Configurable TTL** — balance freshness vs. performance (default 5 minutes)
- **Background Refresh** — optional async/sync indexing on startup via `DATA_INDEXER_RUN_ON_STARTUP`
- **Debug Logging** — detailed real-time visibility of indexing operations with log tags: `[RINEX]`, `[TEC-SUITE]`, `[PARQUET]`, `[PARQUET-SAT]`

### Cache Configuration

The cache database is stored at `DATA_INDEXER_CACHE_DB_PATH` (default: `/app/data/cache.db`) and should be mounted as a volume in `docker-compose.yml`:

```yaml
services:
  data-indexer:
    volumes:
      - cache_data:/app/data  # Persists cache.db across restarts
```

### Viewing Indexing Logs

```bash
# All indexing debug messages
docker compose logs -f data-indexer | grep -E "\[RINEX\]|\[TEC-SUITE\]|\[PARQUET\]"

# Cache hit/miss events
docker compose logs -f data-indexer | grep -i "cache"

# Complete data-indexer logs
docker compose logs -f data-indexer
```

See [data-indexer/README.md](data-indexer/README.md) for detailed configuration and examples.

---

## Testing

All 182 tests pass with full coverage of authentication, job management, data indexing, and converter execution:

```bash
# Install test dependencies (in a venv or dev container)
pip install -r requirements.txt -r requirements-test.txt

# Run all tests
pytest

# Run a specific module
pytest tests/test_runner.py -v
```

Tests use an in-memory SQLite database and fully mock the Docker SDK — no Docker daemon required.

## Key design decisions

**Why HTMX instead of React/Vue?** For a local-network tool used by a small team, HTMX gives you reactive UI (SSE streaming, form submission, partial page swaps) with zero build step and zero JavaScript framework to maintain. The entire frontend is a few Jinja2 templates.

**Why SQLite instead of Postgres?** This service runs on one machine with a handful of concurrent users. SQLite is simpler to operate (a single file in a named volume, no separate service), and it's plenty fast for the workload. Migrating to Postgres later requires only changing `DATABASE_URL`.

**Why mount the Docker socket?** The web service needs to spawn, inspect, and read logs from other containers. Mounting `/var/run/docker.sock` is the standard pattern for this. It does give the container elevated privileges — on a private LAN with trusted users this is acceptable.

**SSE instead of WebSockets?** SSE is a one-way push channel (server → browser) over a plain HTTP connection. It needs no special protocol upgrade, works through most proxies, and is natively supported by all modern browsers. For log streaming, one-way is exactly what's needed.

---

## Environment variables

### Core Service
| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `SECRET_KEY`       | `change-me-in-production-please-32chars!!` | Session cookie signing key — **must change** |
| `ADMIN_PASSWORD`   | `admin`                                  | First-boot admin password                     |
| `DATABASE_URL`     | `sqlite:////app/data/converter_hub.db`   | SQLAlchemy connection string                  |

### Data-Indexer Service
| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `DATA_INDEXER_CACHE_TTL_SEC` | `300.0` | Cache time-to-live in seconds (default 5 minutes) |
| `DATA_INDEXER_CACHE_DB_PATH` | `/app/data/cache.db` | Path to persistent SQLite cache database |
| `DATA_INDEXER_RUN_ON_STARTUP` | `false` | Run indexing on startup: `false` (default), `async`, or `sync` |
| `DATA_INDEXER_TIMEOUT_SEC` | `45` | Timeout for data-indexer HTTP requests |

### Converter Configuration
| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `TECSUITE_IMAGE`   | `tec-suite`                              | Docker image name for TEC-Suite               |
| `DAT_PARQUET_IMAGE`| `dat-parquet-handler`                    | Docker image name for DAT <-> Parquet         |
| `ABSTEC_SUITE_IMAGE`| `abstec-suite:latest`                   | Docker image name for AbsTEC Suite            |

### Data Paths
| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `RINEX_DATA_PATH_HOST` | ``                                    | Host folder root for TEC-suite data (`YYYY_original/DDD/*.zip`) |
| `RINEX_DATA_PATH_CONTAINER` | `/mnt/rinex-server`               | Path inside converter-hub used to browse TEC input folders |
| `TECSUITE_OUT_DAT_DATA_PATH_HOST` | ``                           | Host folder root for TEC DAT output used as AbsTEC input (`YYYY/DDD/SITE/*.dat`) |
| `TECSUITE_OUT_DAT_DATA_PATH_CONTAINER` | `/mnt/tecsuite-out`      | Path inside converter-hub used to browse TEC DAT output folders |
| `TECSUITE_OUT_DAT_DATA_PATH` | `/app/out`                        | Container path for persisted TEC output        |
| `ABSTEC_OUTPUT_DATA_PATH_HOST` | ``                               | Host folder where AbsTEC output is persisted   |
| `ABSTEC_OUTPUT_DATA_PATH` | `/app/abstec_out`                    | Container path for AbsTEC output               |
| `ABSTEC_OUTPUT_DATA_PATH_CONTAINER` | `/mnt/abstec-out`           | Path inside converter-hub used to browse AbsTEC output folders |
| `PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST` | ``                    | Host folder for TEC-Suite Parquet output       |
| `PARQUET_OUTPUT_TECSUITE_DATA_PATH` | `/app/tecsuite_parquet_out` | Container path for TEC-Suite Parquet output    |
| `PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER` | `/mnt/tecsuite-parquet-out` | Path inside converter-hub for browsing TEC-Suite Parquet output |
| `PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST` | ``                       | Host folder for AbsTEC Parquet output          |
| `PARQUET_OUTPUT_ABSTEC_DATA_PATH` | `/app/abstec_parquet_out`    | Container path for AbsTEC Parquet output       |
| `PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER` | `/mnt/abstec-parquet-out` | Path inside converter-hub for browsing AbsTEC Parquet output |

### AbsTEC dockur XP Runner (optional)
Enables the "dockur (Windows XP VM)" option in the AbsTEC Suite **Runner** dropdown. Requires the `abstec-xp` VM from `abstec-suite/docker-compose.dockur.yml` to exist and share the same DAT input / output host paths (see `abstec-suite/README.dockur.md`). When a dockur job is submitted the hub checks the VM container and starts it automatically if it is stopped (a notice is shown, since XP needs a minute or two to boot before the first station runs); if the container does not exist at all, the job is rejected with setup instructions.

| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `ABSTEC_DOCKUR_JOBS_PATH_HOST` | ``                               | Host path of `abstec-suite/dockur/jobs` (the job queue shared with the XP VM). Empty disables the dockur runner. |
| `ABSTEC_DOCKUR_GUEST_DAT_PATH` | `` (runner default `W:\in\`)     | DAT input path as seen from inside the XP guest |
| `ABSTEC_DOCKUR_VM_CONTAINER` | `abstec-xp`                        | Name of the XP VM container the hub auto-starts before dispatching a dockur job |

### Analysis Integration
| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `ANALYSIS_API_BASE_URL` | ``                                   | Base URL of TEC Analysis Backend (e.g. `http://tec-backend:8000`) |
| `ANALYSIS_API_TIMEOUT_SEC` | `45`                              | Timeout for proxied analysis API requests     |

---

## Data Analysis Backend

The **TEC Analysis Backend** is an HTTP service that provides query and visualization endpoints for AbsolTEC and TEC-suite parquet data using DuckDB. It integrates with ConverterHub's analysis proxy and powers data exploration features.

### Core Features

- **Zero-overhead DuckDB queries** — direct parquet file access without intermediate conversions
- **Flexible response formats** — JSON (default), CSV, XLSX for all data endpoints
- **Rich visualization endpoints** — time series, scatter plots, sky-track, multi-station overlays
- **Statistical analysis** — mean, variance, Student-t confidence intervals over day ranges
- **Station metadata** — geodetic coordinates and world-map integration

### API Overview

The service provides endpoints organized in six tags:

#### **AbsolTEC** — 48-point hourly TEC time series
- `GET /absoltec/stations` — Available stations for a year/day
- `GET /absoltec/days` — Available days for a station/year
- `GET /absoltec/raw` — Raw 48-point TEC time series (all 8 columns)
- `GET /absoltec/raw/range` — Multi-day concatenated time series with continuous time axis
- `GET /absoltec/statistics` — Mean ± Student-CI across a day range
- `GET /absoltec/statistics/per-station-day` — Per-day statistics averaged across stations

#### **TEC-suite** — Satellite pass data
- `GET /tec/stations` — Stations with geodetic coordinates (world-map feed)
- `GET /tec/satellites` — Available satellites at a station on a day
- `GET /tec/data` — Full observation time series for one satellite pass
- `GET /tec/raw/range` — Multi-day concatenated satellite observations

#### **Plots** — Visualization endpoints (return PNG, JSON, or Plotly script)
- `/plots/absoltec/average` — Mean TEC with CI/variance error bars
- `/plots/absoltec/day` — Raw or smoothed TEC for a single day
- `/plots/absoltec/multi-station` — Overlay TEC for multiple stations
- `/plots/absoltec/per-station-averages/{doy}` — Average TEC per station group
- `/plots/absoltec/raw/day-by-day` — Raw data over day range with continuous time axis
- `/plots/cb/average` — Mean CB (Coherence Band) with error bars
- `/plots/cb/day` — Raw CB for a single day
- `/plots/cb/multi-station` — CB time series for multiple stations over a day range
- `/plots/cb/vs-tec` — Scatter plot of CB vs AbsolTEC values
- `/plots/cb/per-station-averages/{doy}` — Average CB per station group
- `/plots/tec/satellite` — TEC time series for one satellite on one day
- `/plots/tec/sky-track` — Polar sky-track plot (el/az coloured by TEC)
- `/plots/tec/all-satellites` — Overlay TEC for all available satellites

#### **Stations** — Station discovery and metadata
- `GET /stations/available` — Station codes with data for a year/day (AbsolTEC, TEC-suite, or both)
- `GET /stations/map` — Station metadata (lat/lon/height/ECEF) for world-map visualisation

#### **CB (Coherence Band)** — Coherence band quality indicator
- `GET /cb/stations` — Available stations for a year/day
- `GET /cb/days` — Available days for a station/year
- `GET /cb/raw` — Raw 48-point CB time series
- `GET /cb/raw/range` — Multi-day concatenated CB observations
- `GET /cb/statistics` — Mean CB ± Student-CI across a day range
- `GET /cb/statistics/per-station-day` — Per-day CB statistics averaged across stations

#### **System**
- `GET /health` — Liveness probe (returns 200 OK)

### Query Parameters

All data endpoints support:
- `year` (required) — Year (2000–2100)
- `doy` or `doy_start`/`doy_end` — Day-of-year (1–366)
- `station` or `stations` — Station code(s) to query
- `data_root` (optional) — Override default data root path
- `format` (optional) — Response format: `json` (default), `csv`, or `xlsx`

Plot endpoints additionally support:
- `alpha` — Confidence interval level (default 0.05)
- `show_ci` / `show_var` — Toggle Student-CI and variance error bars
- `width_px`, `height_px`, `dpi` — Plot dimensions
- `format` — `png` (default), `json`, or `script` (Plotly)
- `smooth` — Apply Savitzky-Golay smoothing to AbsolTEC plots
- `poly` — Polynomial order for smoothing (default 3)

### Usage Examples

```bash
# List available stations for a given day
curl "http://tec-backend:8000/stations/available?year=2024&doy=100"

# Raw AbsolTEC data for one station/day (all 8 columns)
curl "http://tec-backend:8000/absoltec/raw?year=2024&doy=100&station=JPLM&format=csv" \
  > tec_data.csv

# Statistics across a week with 95% CI
curl "http://tec-backend:8000/absoltec/statistics?year=2024&doy_start=100&doy_end=106&station=JPLM" \
  | jq '.points | .[].mean_tec'

# Generate a PNG plot (mean TEC with CI)
curl "http://tec-backend:8000/plots/absoltec/average?year=2024&doy_start=100&doy_end=106&station=JPLM" \
  > plot.png

# Multi-day raw data with continuous time axis
curl "http://tec-backend:8000/absoltec/raw/range?year=2024&doy_start=100&doy_end=110&stations=JPLM&stations=USUD&format=json" \
  | jq '.[] | {station, concat_ut, tec}'

# Station map for world-map visualization
curl "http://tec-backend:8000/stations/map?year=2024&doy=100" \
  | jq '.stations[] | {station, lat, lon, height}'
```

### Integration with ConverterHub

ConverterHub proxies requests to the Data Analysis Backend via `ANALYSIS_API_BASE_URL`. All plot and data endpoints are available through the analysis proxy with automatic timeout and error handling.

---

## License

MIT
