"""
config.py — Central settings loaded from environment variables.
All values can be overridden via docker-compose environment section or a .env file.
"""
import os

# Session signing key — MUST be changed in production
SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production-please-32chars!!")

# SQLite database stored in a mounted volume so data survives restarts
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////app/data/converter_hub.db")

# Default admin password set on first boot if no users exist
ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin")

# Docker image name for the tecsuite container
TECSUITE_IMAGE: str = os.getenv("TECSUITE_IMAGE", "tec-suite")

# Host path where TEC-suite RINEX data is stored as YYYY_original/DDD/*.zip
RINEX_DATA_PATH_HOST: str = os.getenv("RINEX_DATA_PATH_HOST", "")

# Path inside converter-hub container where RINEX host folder is mounted for browsing.
RINEX_DATA_PATH_CONTAINER: str = os.getenv("RINEX_DATA_PATH_CONTAINER", "")

# Host path where TEC-suite output should be persisted.
DAT_DATA_PATH_HOST: str = os.getenv("DAT_DATA_PATH_HOST", "")

# Container output path used by TEC-suite image.
DAT_DATA_PATH: str = os.getenv("DAT_DATA_PATH", "/app/out")

# Docker image name for the dat-parquet handler container
DAT_PARQUET_IMAGE: str = os.getenv("DAT_PARQUET_IMAGE", "dat-parquet-handler")

# Docker image name for the AbsTEC Suite container
ABSTEC_SUITE_IMAGE: str = os.getenv("ABSTEC_SUITE_IMAGE", "abstec-suite:latest")

# Minimum time between emitted SSE log lines (seconds)
LOG_EMIT_INTERVAL_SEC: float = float(os.getenv("LOG_EMIT_INTERVAL_SEC", "0.5"))

# How many SSE heartbeat seconds between log lines (keeps connections alive)
SSE_HEARTBEAT_INTERVAL: float = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))

# External data-analysis API integration (TEC backend)
ANALYSIS_API_BASE_URL: str = os.getenv("ANALYSIS_API_BASE_URL", "")
ANALYSIS_API_TIMEOUT_SEC: float = float(os.getenv("ANALYSIS_API_TIMEOUT_SEC", "45"))
