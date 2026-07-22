#!/bin/bash
# Data Indexer Entrypoint Script
# Runs initial indexing then starts the FastAPI server

set -e

echo "Starting Data Indexer Service..."

# The Dockerfile only runs this script when DATA_INDEXER_RUN_ON_STARTUP=sync,
# so accept that as well as "true" — the old `= "true"` test could never match
# here, which meant sync mode silently skipped indexing altogether.
RUN_ON_STARTUP="${DATA_INDEXER_RUN_ON_STARTUP:-false}"
if [ "$RUN_ON_STARTUP" = "true" ] || [ "$RUN_ON_STARTUP" = "sync" ]; then
    # A quoted heredoc keeps this Python out of the shell's hands. The previous
    # inline `python -c "... echo "Warning: ... $e" ... fi"` form closed the
    # shell string early, so `echo`/`fi` landed inside the Python source and
    # $e was expanded by bash.
    python - <<'PY' || echo "Warning: Initial indexing failed; continuing with startup"
import os
import sys

sys.path.insert(0, '/app')
from data_indexer import (
    list_rinex_server_structure,
    list_tecsuite_output_structure,
    list_parquet_output_structure,
    list_parquet_satellite_structure,
    set_last_full_index_time,
    should_run_full_index,
)

DEFAULT_PATHS = {
    'rinex': os.getenv('RINEX_DATA_PATH_CONTAINER', '/mnt/rinex-server'),
    'tecsuite': os.getenv('TECSUITE_OUT_DAT_DATA_PATH_CONTAINER', '/mnt/tecsuite-out'),
    'abstec': os.getenv('ABSTEC_OUTPUT_DATA_PATH_CONTAINER', '/mnt/abstec-out'),
    'parquet_tecsuite': os.getenv('PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER', '/mnt/tecsuite-parquet-out'),
    'parquet_abstec': os.getenv('PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER', '/mnt/abstec-parquet-out'),
}

# Restarting the service must not re-walk the whole RINEX tree when it was just
# indexed; only a sufficiently stale index is rebuilt.
allowed, reason, _age = should_run_full_index()
if not allowed:
    print(f'Skipping initial indexing: {reason}')
    raise SystemExit(0)

print(f'Running initial indexing ({reason})...')
print('Indexing RINEX data...')
list_rinex_server_structure(DEFAULT_PATHS['rinex'])
print('Indexing TEC-suite data...')
list_tecsuite_output_structure(DEFAULT_PATHS['tecsuite'])
print('Indexing Parquet data...')
list_parquet_output_structure(DEFAULT_PATHS['parquet_tecsuite'])
list_parquet_satellite_structure(DEFAULT_PATHS['parquet_tecsuite'])
list_parquet_output_structure(DEFAULT_PATHS['parquet_abstec'])
list_parquet_satellite_structure(DEFAULT_PATHS['parquet_abstec'])
# Only a fully successful pass counts, so a crashed run is retried on the next
# restart instead of being locked out for a day.
set_last_full_index_time()
print('Initial indexing completed successfully')
PY
else
    echo "Skipping initial indexing (DATA_INDEXER_RUN_ON_STARTUP=$RUN_ON_STARTUP)"
fi

# Start the FastAPI server
echo "Starting FastAPI server..."
exec uvicorn app:app --host 0.0.0.0 --port 5001
