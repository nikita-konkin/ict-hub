"""
Data Indexer Service

A Flask-based microservice that provides XML-structured data indexing for:
- RINEX server data
- TEC-suite DAT output data
- AbsTEC output data
- Parquet output data

All endpoints return XML responses.
"""

import os
from flask import Flask, request, Response
import dicttoxml
from data_indexer import (
    list_rinex_server_structure,
    list_tecsuite_output_structure,
    list_parquet_output_structure
)

app = Flask(__name__)

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
    return Response(xml_data, mimetype='application/xml')

@app.route('/health')
def health():
    """Health check endpoint."""
    return {"status": "healthy"}

@app.route('/rinex')
def rinex_index():
    """Get RINEX server structure as XML."""
    root_path = request.args.get('root', DEFAULT_PATHS['rinex'])
    data = list_rinex_server_structure(root_path)
    return dict_to_xml_response(data, "rinex_structure")

@app.route('/tecsuite')
def tecsuite_index():
    """Get TEC-suite DAT output structure as XML."""
    root_path = request.args.get('root', DEFAULT_PATHS['tecsuite'])
    data = list_tecsuite_output_structure(root_path)
    return dict_to_xml_response(data, "tecsuite_structure")

@app.route('/abstec')
def abstec_index():
    """Get AbsTEC output structure as XML (same as tecsuite for now)."""
    root_path = request.args.get('root', DEFAULT_PATHS['abstec'])
    data = list_tecsuite_output_structure(root_path)
    return dict_to_xml_response(data, "abstec_structure")

@app.route('/parquet')
def parquet_index():
    """Get Parquet output structure as XML."""
    root_path = request.args.get('root', DEFAULT_PATHS['parquet_tecsuite'])
    data = list_parquet_output_structure(root_path)
    return dict_to_xml_response(data, "parquet_structure")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)