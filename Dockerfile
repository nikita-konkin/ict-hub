# ── Stage: production image ───────────────────────────────────────────────────
# We use python:3.12-slim (Debian-based) rather than Alpine because the
# docker SDK and passlib[bcrypt] compile native extensions that are far easier
# to build on glibc than musl. The slim variant keeps the final image small
# (~200 MB) while still being easy to work with.
FROM python:3.12-slim

# Set working directory inside the container
WORKDIR /app

# Install OS-level dependencies.
# - gcc and libffi-dev are needed to compile bcrypt's C extension
# - We clean up apt caches immediately to minimise image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
  && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first so Docker can cache this layer.
# If requirements.txt hasn't changed, pip install is skipped on subsequent builds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application source
COPY app/ ./app/

# Create directories that must exist at runtime.
# /app/data       → SQLite database (mounted as a named volume)
# /app/app/static → Starlette StaticFiles mount point (must exist even if empty)
# Baked in at build time so they can never go missing.
RUN mkdir -p /app/data /app/app/static

# Expose the HTTP port Uvicorn listens on
EXPOSE 8000

# Health check: poll the root endpoint every 30s.
# Docker marks the container unhealthy after 3 consecutive failures.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/login')" \
  || exit 1

# Default command: Uvicorn with one worker.
# --host 0.0.0.0 binds to all interfaces (required for Docker networking).
# --no-access-log keeps the log clean; structured logs come from our app logger.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
