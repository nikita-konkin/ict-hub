"""
Data Indexer Service

A FastAPI-based microservice that provides XML-structured data indexing for:
- RINEX server data
- TEC-suite DAT output data
- AbsTEC output data
- Parquet output data

All endpoints return XML responses.

Configuration:
- DATA_INDEXER_CACHE_TTL_SEC: Cache TTL in seconds (default: 300.0 = 5 minutes)
- DATA_INDEXER_CACHE_DB_PATH: Path to persistent cache database (default: /app/data/cache.db)
- DATA_INDEXER_RUN_ON_STARTUP: Run initial indexing on startup (default: false)
- INDEXER_RINEX_DATA_PATH_CONTAINER: RINEX data path (default: /mnt/rinex-server)
- INDEXER_TECSUITE_OUT_DAT_DATA_PATH_CONTAINER: TEC-suite data path (default: /mnt/tecsuite-out)
- INDEXER_ABSTEC_OUTPUT_DATA_PATH_CONTAINER: AbsTEC data path (default: /mnt/abstec-out)
- INDEXER_PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER: TEC-suite parquet path (default: /mnt/tecsuite-parquet-out)
- INDEXER_PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER: AbsTEC parquet path (default: /mnt/abstec-parquet-out)
"""

import os
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
import dicttoxml
import logging
from data_indexer import (
    list_rinex_server_structure,
    list_tecsuite_output_structure,
    list_abstec_output_structure,
    list_parquet_output_structure,
    list_parquet_satellite_structure,
    stop_all_watchers,
)

# Configure logging to show DEBUG messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True  # Force reconfiguration
)

# Reduce verbosity of third-party libraries
logging.getLogger('dicttoxml').setLevel(logging.WARNING)

# Set up logger for this module
logger = logging.getLogger(__name__)

app = FastAPI(title="Data Indexer Service")

# Default paths from environment variables
DEFAULT_PATHS = {
    'rinex': os.getenv('INDEXER_RINEX_DATA_PATH_CONTAINER', '/mnt/rinex-server'),
    'tecsuite': os.getenv('INDEXER_TECSUITE_OUT_DAT_DATA_PATH_CONTAINER', '/mnt/tecsuite-out'),
    'abstec': os.getenv('INDEXER_ABSTEC_OUTPUT_DATA_PATH_CONTAINER', '/mnt/abstec-out'),
    'parquet_tecsuite': os.getenv('INDEXER_PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER', '/mnt/tecsuite-parquet-out'),
    'parquet_abstec': os.getenv('INDEXER_PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER', '/mnt/abstec-parquet-out')
}

def dict_to_xml_response(data, root_element="data"):
    """Convert dictionary to XML response."""
    xml_data = dicttoxml.dicttoxml(data, custom_root=root_element, attr_type=False)
    return Response(content=xml_data, media_type="application/xml")

@app.on_event("startup")
async def startup_event():
    """Run initial indexing on startup if enabled."""
    if os.getenv('DATA_INDEXER_RUN_ON_STARTUP', 'false').lower() == 'true':
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        async def index_all():
            logger.info("Running initial indexing on startup...")
            try:
                # Index all data types to warm up caches
                list_rinex_server_structure(DEFAULT_PATHS['rinex'])
                list_tecsuite_output_structure(DEFAULT_PATHS['tecsuite'])
                list_abstec_output_structure(DEFAULT_PATHS['abstec'])
                list_parquet_output_structure(DEFAULT_PATHS['parquet_tecsuite'])
                list_parquet_satellite_structure(DEFAULT_PATHS['parquet_tecsuite'])
                list_parquet_output_structure(DEFAULT_PATHS['parquet_abstec'])
                list_parquet_satellite_structure(DEFAULT_PATHS['parquet_abstec'])
                logger.info("Initial indexing completed successfully")
            except Exception as e:
                logger.error(f"Initial indexing failed: {e}")

        # Run indexing in background to not block startup
        asyncio.create_task(index_all())


@app.on_event("shutdown")
async def shutdown_event():
    """Stop filesystem watchers cleanly on service shutdown."""
    stop_all_watchers()

@app.get('/health')
def health():
    """Health check endpoint."""
    return JSONResponse(content={"status": "healthy"})

@app.get('/status')
def indexer_status():
    """Get data indexer status and cache information."""
    import time
    from data_indexer import (
        _CACHE_TTL_SEC,
        _rinex_cache, _tecsuite_cache, _abstec_cache, _parquet_cache, _parquet_sat_cache
    )

    now = time.monotonic()
    cache_info = {
        "rinex": {
            "entries": len(_rinex_cache),
            "ttl_seconds": _CACHE_TTL_SEC
        },
        "tecsuite": {
            "entries": len(_tecsuite_cache),
            "ttl_seconds": _CACHE_TTL_SEC
        },
        "abstec": {
            "entries": len(_abstec_cache),
            "ttl_seconds": _CACHE_TTL_SEC
        },
        "parquet": {
            "entries": len(_parquet_cache),
            "ttl_seconds": _CACHE_TTL_SEC
        },
        "parquet_satellite": {
            "entries": len(_parquet_sat_cache),
            "ttl_seconds": _CACHE_TTL_SEC
        }
    }

    return JSONResponse(content={
        "status": "healthy",
        "cache_info": cache_info,
        "timestamp": time.time()
    })

@app.get('/rinex')
def rinex_index(root: str = Query(default=DEFAULT_PATHS['rinex'])):
    """Get RINEX server structure as XML."""
    
    logger.info(f"[APP] RINEX endpoint called with root: {root}")
    data = list_rinex_server_structure(root)
    
    logger.info(f"[APP] RINEX indexing completed, returning {len(data)} years")
    return dict_to_xml_response(data, "rinex_structure")

@app.get('/tecsuite')
def tecsuite_index(root: str = Query(default=DEFAULT_PATHS['tecsuite'])):
    """Get TEC-suite DAT output structure as XML."""
    data = list_tecsuite_output_structure(root)
    return dict_to_xml_response(data, "tecsuite_structure")

@app.get('/abstec')
def abstec_index(root: str = Query(default=DEFAULT_PATHS['abstec'])):
    """Get AbsTEC output structure as XML."""
    data = list_abstec_output_structure(root)
    return dict_to_xml_response(data, "abstec_structure")

@app.get('/parquet')
def parquet_index(root: str = Query(default=DEFAULT_PATHS['parquet_tecsuite'])):
    """Get Parquet output structure as XML."""
    data = list_parquet_output_structure(root)
    return dict_to_xml_response(data, "parquet_structure")


@app.get('/parquet-satellites')
def parquet_satellite_index(root: str = Query(default=DEFAULT_PATHS['parquet_tecsuite'])):
    """Get Parquet output structure with stations/satellites as XML."""
    data = list_parquet_satellite_structure(root)
    return dict_to_xml_response(data, "parquet_satellite_structure")


@app.get('/parquet/tecsuite')
def parquet_tecsuite_index():
    """Get TEC-suite parquet output structure as XML."""
    data = list_parquet_output_structure(DEFAULT_PATHS['parquet_tecsuite'])
    return dict_to_xml_response(data, "parquet_structure")


@app.get('/parquet/abstec')
def parquet_abstec_index():
    """Get AbsTEC parquet output structure as XML."""
    data = list_parquet_output_structure(DEFAULT_PATHS['parquet_abstec'])
    return dict_to_xml_response(data, "parquet_structure")


@app.get('/parquet-satellites/tecsuite')
def parquet_tecsuite_satellite_index():
    """Get TEC-suite parquet structure with stations/satellites as XML."""
    data = list_parquet_satellite_structure(DEFAULT_PATHS['parquet_tecsuite'])
    return dict_to_xml_response(data, "parquet_satellite_structure")


@app.get('/parquet-satellites/abstec')
def parquet_abstec_satellite_index():
    """Get AbsTEC parquet structure with stations/satellites as XML."""
    data = list_parquet_satellite_structure(DEFAULT_PATHS['parquet_abstec'])
    return dict_to_xml_response(data, "parquet_satellite_structure")

if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host='0.0.0.0', port=5001)
