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

# How many SSE heartbeat seconds between log lines (keeps connections alive)
SSE_HEARTBEAT_INTERVAL: float = float(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))
