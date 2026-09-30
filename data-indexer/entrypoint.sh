#!/bin/sh
# Data Indexer Entrypoint Script
#
# DATA_INDEXER_RUN_ON_STARTUP:
#   false (default)  start the server, index on first request
#   true | async     start the server, index in a background thread (app.py)
#   sync             index every configured root first, then start the server

set -e

echo "Starting Data Indexer Service..."

mode=$(printf '%s' "${DATA_INDEXER_RUN_ON_STARTUP:-false}" | tr '[:upper:]' '[:lower:]')

if [ "$mode" = "sync" ]; then
    echo "Running initial indexing before startup..."
    # Results land in the persistent cache DB, which the server loads on start.
    python -c "import app; app.warm_up()" || echo "Warning: initial indexing failed; continuing startup"
else
    echo "Skipping blocking initial indexing (DATA_INDEXER_RUN_ON_STARTUP=$mode)"
fi

echo "Starting FastAPI server..."
exec uvicorn app:app --host 0.0.0.0 --port 5001
