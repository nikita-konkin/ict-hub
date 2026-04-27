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
