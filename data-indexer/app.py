"""
Data Indexer Service

A FastAPI-based microservice that provides XML-structured data indexing for:
- RINEX server data
- TEC-suite DAT output data
- AbsTEC output data
- Parquet output data

All endpoints return XML responses.
"""

import os
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
import dicttoxml
from data_indexer import (
    list_rinex_server_structure,
    list_tecsuite_output_structure,
    list_parquet_output_structure,
    list_parquet_satellite_structure,
)

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

@app.get('/health')
def health():
    """Health check endpoint."""
    return JSONResponse(content={"status": "healthy"})

@app.get('/rinex')
def rinex_index(root: str = Query(default=DEFAULT_PATHS['rinex'])):
    """Get RINEX server structure as XML."""
    data = list_rinex_server_structure(root)
    return dict_to_xml_response(data, "rinex_structure")

@app.get('/tecsuite')
def tecsuite_index(root: str = Query(default=DEFAULT_PATHS['tecsuite'])):
    """Get TEC-suite DAT output structure as XML."""
    data = list_tecsuite_output_structure(root)
    return dict_to_xml_response(data, "tecsuite_structure")

@app.get('/abstec')
def abstec_index(root: str = Query(default=DEFAULT_PATHS['abstec'])):
    """Get AbsTEC output structure as XML (same as tecsuite for now)."""
    data = list_tecsuite_output_structure(root)
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