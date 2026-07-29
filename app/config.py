"""
config.py — Central settings loaded from environment variables.
All values can be overridden via docker-compose environment section or a .env file.
"""
import logging
import os
import secrets

logger = logging.getLogger(__name__)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# ── Session signing key ───────────────────────────────────────────────────────
# The session cookie is signed with this key. If it is known to an attacker they
# can forge a cookie and impersonate any user (including admin), so a leaked or
# placeholder key is a full authentication bypass.
#
# Known-insecure values that must never be used to sign real sessions. These are
# the historical placeholder defaults shipped in source / compose / .env.example.
INSECURE_SECRET_KEYS: frozenset[str] = frozenset({
    "",
    "change-me-in-production-please-32chars!!",
    "replace-me-with-a-random-32-char-string!!",
    "change-me-in-production",
    "changeme",
    "secret",
})

# Minimum acceptable length for an operator-supplied key.
_MIN_SECRET_KEY_LEN = 32

# When enabled (recommended for any networked/production deployment, set in
# docker-compose.yml) the app refuses to start with a weak/placeholder key
# instead of silently falling back to an ephemeral one.
REQUIRE_STRONG_SECRET: bool = _is_truthy(os.getenv("REQUIRE_STRONG_SECRET"))


def _resolve_secret_key() -> str:
    raw = os.getenv("SECRET_KEY", "").strip()
    is_weak = raw in INSECURE_SECRET_KEYS or len(raw) < _MIN_SECRET_KEY_LEN
    if not is_weak:
        return raw

    if REQUIRE_STRONG_SECRET:
        raise RuntimeError(
            "SECRET_KEY is unset, too short (<%d chars), or a known placeholder. "
            "Generate a strong random value and set it in .env:\n"
            '    python -c "import secrets; print(secrets.token_hex(32))"\n'
            "(REQUIRE_STRONG_SECRET is enabled, so booting with a weak key is refused.)"
            % _MIN_SECRET_KEY_LEN
        )

    # Local/dev/test fallback: an EPHEMERAL random key. This is unguessable, so
    # cookie forgery is impossible — but sessions do not survive a restart and
    # multiple workers would not share it. Set SECRET_KEY in .env for stable
    # sessions. We deliberately do NOT fall back to the known placeholder.
    logger.warning(
        "SECRET_KEY is unset or weak; generated an EPHEMERAL random signing key. "
        "Sessions will not persist across restarts. Set a strong SECRET_KEY in .env "
        "for stable sessions (and REQUIRE_STRONG_SECRET=1 in production)."
    )
    return secrets.token_hex(32)


SECRET_KEY: str = _resolve_secret_key()

# SQLite database stored in a mounted volume so data survives restarts
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////app/data/converter_hub.db")

# ── Default admin bootstrap ───────────────────────────────────────────────────
# Password for the 'admin' user seeded on first boot when no users exist.
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin")

# Passwords considered trivially guessable. A seeded admin using one of these
# (or any password < 8 chars) is flagged must_change_password so the operator is
# forced to rotate it on first login instead of silently running admin/admin.
WEAK_ADMIN_PASSWORDS: frozenset[str] = frozenset({
    "admin", "password", "changeme", "admin123", "root", "letmein", "12345678",
})


def is_weak_admin_password(password: str) -> bool:
    p = str(password or "")
    return len(p) < 8 or p.lower() in WEAK_ADMIN_PASSWORDS


# ── Transport / cookie security ───────────────────────────────────────────────
# Set SESSION_COOKIE_SECURE=1 ONLY when the app is served over HTTPS (e.g. behind
# a TLS reverse proxy). It stays OFF by default so plain-HTTP local deployments
# keep working — a Secure cookie is never sent over HTTP and would lock users out.
SESSION_COOKIE_SECURE: bool = _is_truthy(os.getenv("SESSION_COOKIE_SECURE"))

# Session inactivity lifetime (seconds). Default 24h.
SESSION_MAX_AGE_SEC: int = int(os.getenv("SESSION_MAX_AGE_SEC", "86400"))

# Response security headers (X-Frame-Options, CSP, nosniff, …). On by default.
SECURITY_HEADERS_ENABLED: bool = os.getenv("SECURITY_HEADERS_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

# Reject cross-origin state-changing requests (browser CSRF defense) by comparing
# the Origin/Referer host to the request host. On by default; harmless for
# same-origin browser use and for non-browser clients (which omit Origin).
CSRF_ORIGIN_CHECK_ENABLED: bool = os.getenv("CSRF_ORIGIN_CHECK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

# ── Login rate limiting (brute-force / credential-stuffing defense) ───────────
LOGIN_RATE_LIMIT_ENABLED: bool = os.getenv("LOGIN_RATE_LIMIT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
# Max failed attempts per (client IP, username) within the window before lockout.
LOGIN_RATE_LIMIT_MAX_ATTEMPTS: int = int(os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "8"))
# Sliding window / lockout duration in seconds.
LOGIN_RATE_LIMIT_WINDOW_SEC: int = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "300"))

# Docker image name for the tecsuite container. Defaults must stay namespaced and
# tagged: converter-hub pulls these through the Docker socket, and a bare name
# resolves to docker.io/library/<name>, which 404s instead of failing usefully.
TECSUITE_IMAGE: str = os.getenv("TECSUITE_IMAGE", "nikitaikonkin/tec-suite:latest")

# Host path where TEC-suite RINEX data is stored as YYYY_original/DDD/*.zip
RINEX_DATA_PATH_HOST: str = os.getenv("RINEX_DATA_PATH_HOST", "")

# Path inside converter-hub container where RINEX host folder is mounted for browsing.
RINEX_DATA_PATH_CONTAINER: str = os.getenv("RINEX_DATA_PATH_CONTAINER", "")

# Host path where TEC-suite output should be persisted.
TECSUITE_OUT_DAT_DATA_PATH_HOST: str = os.getenv("TECSUITE_OUT_DAT_DATA_PATH_HOST", "")

# Path inside converter-hub container used to browse TEC DAT output folders.
TECSUITE_OUT_DAT_DATA_PATH_CONTAINER: str = os.getenv("TECSUITE_OUT_DAT_DATA_PATH_CONTAINER", "")

# Container output path used by TEC-suite image.
TECSUITE_OUT_DAT_DATA_PATH: str = os.getenv("TECSUITE_OUT_DAT_DATA_PATH", "/app/out")

# Docker image name for the dat-parquet handler container
DAT_PARQUET_IMAGE: str = os.getenv(
    "DAT_PARQUET_IMAGE", "nikitaikonkin/dat-parquet-handler:latest"
)

# Docker image name for the AbsTEC Suite container
ABSTEC_SUITE_IMAGE: str = os.getenv(
    "ABSTEC_SUITE_IMAGE", "nikitaikonkin/abstec-suite:latest"
)

# Host path where AbsTEC output should be persisted.
ABSTEC_OUTPUT_DATA_PATH_HOST: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH_HOST", "")

# Container output path used by AbsTEC image.
ABSTEC_OUTPUT_DATA_PATH: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH", "/app/abstec_out")

# Path inside converter-hub container used to browse AbsTEC output folders.
ABSTEC_OUTPUT_DATA_PATH_CONTAINER: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH_CONTAINER", "")

# Host path of the dockur XP runner's shared jobs directory (abstec-suite/dockur/jobs,
# also mounted into the XP VM as /shared/jobs). Required for the "dockur" runner
# option of the AbsTEC Suite converter.
ABSTEC_DOCKUR_JOBS_PATH_HOST: str = os.getenv("ABSTEC_DOCKUR_JOBS_PATH_HOST", "")

# Optional: DAT input path as seen from inside the XP guest (dia line 1).
# Leave empty to use the runner's default (W:\in\).
ABSTEC_DOCKUR_GUEST_DAT_PATH: str = os.getenv("ABSTEC_DOCKUR_GUEST_DAT_PATH", "")

# Name of the dockur XP VM container (abstec-suite/docker-compose.dockur.yml).
# Before dispatching a dockur job the hub checks this container and starts it
# if it exists but is stopped, so the guest watcher can pick the job up.
ABSTEC_DOCKUR_VM_CONTAINER: str = os.getenv("ABSTEC_DOCKUR_VM_CONTAINER", "abstec-xp")

# Host path where DAT -> Parquet output from TEC-Suite input should be persisted.
PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST: str = os.getenv("PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST", "")

# Optional path inside the dat-parquet container for the TEC-Suite parquet output mount.
PARQUET_OUTPUT_TECSUITE_DATA_PATH: str = os.getenv("PARQUET_OUTPUT_TECSUITE_DATA_PATH", "")

# Path inside converter-hub container used to browse TEC-Suite parquet output folders.
PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER: str = os.getenv("PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "")

# Host path where DAT -> Parquet output from AbsTEC input should be persisted.
PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST: str = os.getenv("PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST", "")

# Optional path inside the dat-parquet container for the AbsTEC parquet output mount.
PARQUET_OUTPUT_ABSTEC_DATA_PATH: str = os.getenv("PARQUET_OUTPUT_ABSTEC_DATA_PATH", "")

# Path inside converter-hub container used to browse AbsTEC parquet output folders.
PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER: str = os.getenv("PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", "")

# Minimum time between emitted SSE log lines (seconds)
LOG_EMIT_INTERVAL_SEC: float = float(os.getenv("LOG_EMIT_INTERVAL_SEC", "0.5"))

# Max number of log lines kept in the browser UI per running job panel.
# Older lines are trimmed as new ones arrive.
try:
    LOG_MAX_LINES: int = int(os.getenv("LOG_MAX_LINES", "2000"))
except ValueError:
    LOG_MAX_LINES = 2000
LOG_MAX_LINES = max(1, LOG_MAX_LINES)

# How many log lines to request on a full page render (browser reload / opening
# an existing job panel). This avoids re-sending the entire job history when the
# job produced a lot of output.
try:
    LOG_PAGELOAD_TAIL_LINES: int = int(os.getenv("LOG_PAGELOAD_TAIL_LINES", "200"))
except ValueError:
    LOG_PAGELOAD_TAIL_LINES = 200
LOG_PAGELOAD_TAIL_LINES = max(0, LOG_PAGELOAD_TAIL_LINES)
LOG_PAGELOAD_TAIL_LINES = min(LOG_PAGELOAD_TAIL_LINES, LOG_MAX_LINES)

# How many SSE heartbeat seconds between log lines (keeps connections alive)
SSE_HEARTBEAT_INTERVAL: float = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))

# Maximum number of persisted durable log events per job (DB retention).
# This is separate from LOG_MAX_LINES (UI trimming), but defaults to the same
# value for reasonable behavior out of the box.
try:
    JOB_EVENT_LOG_MAX_LINES: int = int(os.getenv("JOB_EVENT_LOG_MAX_LINES", str(LOG_MAX_LINES)))
except ValueError:
    JOB_EVENT_LOG_MAX_LINES = LOG_MAX_LINES
JOB_EVENT_LOG_MAX_LINES = max(0, JOB_EVENT_LOG_MAX_LINES)

# Enable the background job runtime that tails container logs and reconciles
# finished containers into durable job events. Tests can disable this.
JOB_RUNTIME_ENABLED: bool = os.getenv("JOB_RUNTIME_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

# Polling interval for running-job reconciliation and producer restart checks.
JOB_MONITOR_INTERVAL_SEC: float = float(os.getenv("JOB_MONITOR_INTERVAL_SEC", "3"))

# Discover and import running converter containers from Docker when rendering pages.
# This is useful after resetting/rebuilding ict-hub while converter containers are
# still running.
DISCOVER_RUNNING_CONTAINERS: bool = os.getenv("DISCOVER_RUNNING_CONTAINERS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

# Polling interval for SSE consumers reading persisted job events.
JOB_EVENT_POLL_INTERVAL_SEC: float = float(os.getenv("JOB_EVENT_POLL_INTERVAL_SEC", "0.5"))

# Maximum time to wait for the durable job-event producer to emit its first
# event before the SSE endpoint falls back to direct container log streaming.
JOB_EVENT_BOOTSTRAP_TIMEOUT_SEC: float = float(os.getenv("JOB_EVENT_BOOTSTRAP_TIMEOUT_SEC", "1.5"))

# External data-analysis API integration (TEC backend)
ANALYSIS_API_BASE_URL: str = os.getenv("ANALYSIS_API_BASE_URL", "")
ANALYSIS_API_TIMEOUT_SEC: float = float(os.getenv("ANALYSIS_API_TIMEOUT_SEC", "45"))

# External data-indexer FastAPI integration
DATA_INDEXER_URL: str = os.getenv("DATA_INDEXER_URL", "")
DATA_INDEXER_TIMEOUT_SEC: float = float(os.getenv("DATA_INDEXER_TIMEOUT_SEC", "120"))

# Optional TEC map basemap sources.
# CACHE_ROOT stores fetched OpenStreetMap raster tiles under openstreetmap/z/x/y.png.
# TILE_SERVER_URL is an HTTP XYZ tile-server URL template with {z}/{x}/{y} placeholders,
# e.g. http://localhost:8090/tile/{z}/{x}/{y}.png for a self-hosted overv/openstreetmap-tile-server.
TEC_MAP_BASEMAP_CACHE_ROOT: str = os.getenv("TEC_MAP_BASEMAP_CACHE_ROOT", "/app/data/basemap_cache")
TEC_MAP_BASEMAP_TILE_SERVER_URL: str = os.getenv("TEC_MAP_BASEMAP_TILE_SERVER_URL", "")

# If enabled, TEC map rendering falls back to the plain lat/lon view when the selected
# basemap source is unavailable instead of failing the whole GIF request.
TEC_MAP_BASEMAP_FALLBACK_TO_PLAIN: bool = os.getenv("TEC_MAP_BASEMAP_FALLBACK_TO_PLAIN", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
