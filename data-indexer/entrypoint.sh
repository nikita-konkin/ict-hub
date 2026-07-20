#!/bin/bash
# Data Indexer Entrypoint Script
# Runs initial indexing then starts the FastAPI server

set -e

echo "Starting Data Indexer Service..."

# Run initial indexing if enabled
if [ "${DATA_INDEXER_RUN_ON_STARTUP:-false}" = "true" ]; then
    echo "Running initial indexing..."
    python -c "
import os
import sys
sys.path.insert(0, '/app')
from data_indexer import (
    list_rinex_server_structure,
    list_tecsuite_output_structure,
    list_parquet_output_structure,
    list_parquet_satellite_structure,
)

DEFAULT_PATHS = {
    'rinex': os.getenv('RINEX_DATA_PATH_CONTAINER', '/mnt/rinex-server'),
    'tecsuite': os.getenv('TECSUITE_OUT_DAT_DATA_PATH_CONTAINER', '/mnt/tecsuite-out'),
    'abstec': os.getenv('ABSTEC_OUTPUT_DATA_PATH_CONTAINER', '/mnt/abstec-out'),
    'parquet_tecsuite': os.getenv('PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER', '/mnt/tecsuite-parquet-out'),
    'parquet_abstec': os.getenv('PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER', '/mnt/abstec-parquet-out')
}

try:
    print('Indexing RINEX data...')
    list_rinex_server_structure(DEFAULT_PATHS['rinex'])
    print('Indexing TEC-suite data...')
    list_tecsuite_output_structure(DEFAULT_PATHS['tecsuite'])
    print('Indexing Parquet data...')
    list_parquet_output_structure(DEFAULT_PATHS['parquet_tecsuite'])
    list_parquet_satellite_structure(DEFAULT_PATHS['parquet_tecsuite'])
    list_parquet_output_structure(DEFAULT_PATHS['parquet_abstec'])
    list_parquet_satellite_structure(DEFAULT_PATHS['parquet_abstec'])
    print('Initial indexing completed successfully')
except Exception as e:
    echo "Warning: Initial indexing failed: $e"
    # Don't exit - continue with startup
fi
else
    echo "Skipping initial indexing (DATA_INDEXER_RUN_ON_STARTUP=false)"
fi

# Start the FastAPI server
echo "Starting FastAPI server..."
exec uvicorn app:app --host 0.0.0.0 --port 5001