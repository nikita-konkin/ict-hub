"""
config.py — Central settings loaded from environment variables.
All values can be overridden via docker-compose environment section or a .env file.
"""
import logging
import os
import secrets

from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

# SQLite database stored in a mounted volume so data survives restarts
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////app/data/converter_hub.db")

# Values that shipped as examples in this repository. Anyone can sign a session
# cookie with them, so they are treated exactly like an unset key.
_PLACEHOLDER_SECRET_KEYS = {
    "",
    "change-me-in-production-please-32chars!!",
    "replace-me-with-a-random-32-char-string!!",
}


def _default_secret_key_file() -> str:
    """Keep a generated key next to the SQLite database, which lives on a volume."""
    try:
        database = make_url(DATABASE_URL).database
    except Exception:
        return ""
    if not database or database == ":memory:":
        return ""
    return os.path.join(os.path.dirname(os.path.abspath(database)), ".secret_key")


def _resolve_secret_key() -> str:
    """
    Return SECRET_KEY from the environment, or a random key persisted to
    SECRET_KEY_FILE when the environment holds nothing or a published placeholder.
    """
    configured = os.getenv("SECRET_KEY", "").strip()
    if configured not in _PLACEHOLDER_SECRET_KEYS:
        return configured

    key_file = os.getenv("SECRET_KEY_FILE", "").strip() or _default_secret_key_file()
    if key_file:
        try:
            with open(key_file, encoding="utf-8") as fh:
                stored = fh.read().strip()
            if stored:
                return stored
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Cannot read SECRET_KEY_FILE %s: %s", key_file, exc)

    generated = secrets.token_urlsafe(48)
    if key_file:
        try:
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(generated)
            logger.warning(
                "SECRET_KEY is unset or a placeholder; generated a random key in %s", key_file
            )
            return generated
        except FileExistsError:
            # Another worker created it first — use theirs so sessions agree.
            with open(key_file, encoding="utf-8") as fh:
                return fh.read().strip() or generated
        except OSError as exc:
            logger.warning("Cannot write SECRET_KEY_FILE %s: %s", key_file, exc)

    logger.warning(
        "SECRET_KEY is unset or a placeholder; using a temporary random key. "
        "Sessions will not survive a restart."
    )
    return generated


# Session signing key. Set SECRET_KEY explicitly, or leave it empty to have a
# random key generated and stored in SECRET_KEY_FILE (default: next to the DB).
SECRET_KEY: str = _resolve_secret_key()

# Default admin password set on first boot if no users exist
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin")

# Docker image name for the tecsuite container
TECSUITE_IMAGE: str = os.getenv("TECSUITE_IMAGE", "tec-suite")

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
DAT_PARQUET_IMAGE: str = os.getenv("DAT_PARQUET_IMAGE", "dat-parquet-handler:latest")

# Docker image name for the AbsTEC Suite container
ABSTEC_SUITE_IMAGE: str = os.getenv("ABSTEC_SUITE_IMAGE", "abstec-suite:latest")

# Host path where AbsTEC output should be persisted.
ABSTEC_OUTPUT_DATA_PATH_HOST: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH_HOST", "")

# Container output path used by AbsTEC image.
ABSTEC_OUTPUT_DATA_PATH: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH", "/app/abstec_out")

# Path inside converter-hub container used to browse AbsTEC output folders.
ABSTEC_OUTPUT_DATA_PATH_CONTAINER: str = os.getenv("ABSTEC_OUTPUT_DATA_PATH_CONTAINER", "")

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

# How many SSE heartbeat seconds between log lines (keeps connections alive)
SSE_HEARTBEAT_INTERVAL: float = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))

# External data-analysis API integration (TEC backend)
ANALYSIS_API_BASE_URL: str = os.getenv("ANALYSIS_API_BASE_URL", "")
ANALYSIS_API_TIMEOUT_SEC: float = float(os.getenv("ANALYSIS_API_TIMEOUT_SEC", "45"))

# External data-indexer FastAPI integration
DATA_INDEXER_URL: str = os.getenv("DATA_INDEXER_URL", "")
DATA_INDEXER_TIMEOUT_SEC: float = float(os.getenv("DATA_INDEXER_TIMEOUT_SEC", "120"))

# How long page renders reuse a data-indexer response before asking again.
DATA_INDEXER_CLIENT_CACHE_TTL_SEC: float = float(os.getenv("DATA_INDEXER_CLIENT_CACHE_TTL_SEC", "30"))
