# ConverterHub

A lightweight, luxury-minimal web interface for running Docker-based data converters on your local network. Built with FastAPI + HTMX for real-time log streaming without a heavy JavaScript framework.

---

## Features

- **Real-time log streaming** via Server-Sent Events (SSE) — no polling
- **Progress bar** parsed automatically from container log output
- **Session-based authentication** with bcrypt-hashed passwords
- **Role system**: `admin` (full access) and `operator` (own jobs only)
- **Audit log** of every job: who ran what, when, with which flags, and the exit code
- **Extensible converter registry** — adding a new converter is a single dict entry
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

The UI is available at **http://localhost:8080** (or any LAN IP on port 8080).

### 2. First login

On first boot a default `admin` account is created with the password from the `ADMIN_PASSWORD` environment variable (default: `admin`). **Change it immediately** via the Users page.

### 3. Running a TEC-Suite job

1. Log in as `admin` (or any active user).
2. Click **TEC-Suite** on the dashboard.
3. Fill in the host-side RINEX directory path and output directory path.
4. Adjust parallel jobs, verbose, cleanup, and optional auto-remove (`--rm`) flag.
5. Click **Run** — the log panel appears in real time on the right side of the screen.

---

## Architecture overview

```
converter-hub/
├── Dockerfile                   # Python 3.12-slim image for the web service
├── docker-compose.yml           # Orchestrates the service + named volume for DB
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
    └── templates/               # Jinja2 templates (Cormorant Garamond + DM Sans)
        ├── base.html            # Sidebar layout, all CSS, HTMX scripts
        ├── login.html
        ├── dashboard.html
        ├── run.html             # Converter form page
        ├── job_panel.html       # HTMX fragment — SSE monitor panel
        ├── history.html
        └── users.html

tests/
├── conftest.py                  # In-memory DB, mock fixtures, authenticated clients
├── test_auth.py                 # Login, logout, session, access control
├── test_jobs.py                 # Job creation, history access control (Docker mocked)
└── test_runner.py               # Progress parsing, command building, Docker SDK mocking
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

## Running tests

```bash
# Install test dependencies (in a venv or dev container)
pip install -r requirements.txt -r requirements-test.txt

# Run all tests
pytest

# Run a specific module
pytest tests/test_runner.py -v
```

Tests use an in-memory SQLite database and fully mock the Docker SDK — no Docker daemon required.

---

## Key design decisions

**Why HTMX instead of React/Vue?** For a local-network tool used by a small team, HTMX gives you reactive UI (SSE streaming, form submission, partial page swaps) with zero build step and zero JavaScript framework to maintain. The entire frontend is a few Jinja2 templates.

**Why SQLite instead of Postgres?** This service runs on one machine with a handful of concurrent users. SQLite is simpler to operate (a single file in a named volume, no separate service), and it's plenty fast for the workload. Migrating to Postgres later requires only changing `DATABASE_URL`.

**Why mount the Docker socket?** The web service needs to spawn, inspect, and read logs from other containers. Mounting `/var/run/docker.sock` is the standard pattern for this. It does give the container elevated privileges — on a private LAN with trusted users this is acceptable.

**SSE instead of WebSockets?** SSE is a one-way push channel (server → browser) over a plain HTTP connection. It needs no special protocol upgrade, works through most proxies, and is natively supported by all modern browsers. For log streaming, one-way is exactly what's needed.

---

## Environment variables

| Variable           | Default                                  | Description                                   |
|--------------------|------------------------------------------|-----------------------------------------------|
| `SECRET_KEY`       | `change-me-in-production-please-32chars!!` | Session cookie signing key — **must change** |
| `ADMIN_PASSWORD`   | `admin`                                  | First-boot admin password                     |
| `DATABASE_URL`     | `sqlite:////app/data/converter_hub.db`   | SQLAlchemy connection string                  |
| `TECSUITE_IMAGE`   | `tecsuite`                               | Docker image name for TEC-Suite               |

---

## License

MIT
