# Data Indexer Service

A microservice that provides XML-structured data indexing for GNSS data processing.

## Environment Variables

The service uses the following environment variables (set in `.env` file):

### Data Paths
- `INDEXER_RINEX_DATA_PATH_HOST`: Host path to RINEX data directory
- `INDEXER_RINEX_DATA_PATH_CONTAINER`: Container path for RINEX data (default: `/mnt/rinex-server`)
- `INDEXER_TECSUITE_OUT_DAT_DATA_PATH_HOST`: Host path to TEC-suite DAT output
- `INDEXER_TECSUITE_OUT_DAT_DATA_PATH_CONTAINER`: Container path for TEC-suite DAT (default: `/mnt/tecsuite-out`)
- `INDEXER_ABSTEC_OUTPUT_DATA_PATH_HOST`: Host path to AbsTEC output
- `INDEXER_ABSTEC_OUTPUT_DATA_PATH_CONTAINER`: Container path for AbsTEC (default: `/mnt/abstec-out`)
- `INDEXER_PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST`: Host path to TEC-suite Parquet output
- `INDEXER_PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER`: Container path for TEC-suite Parquet (default: `/mnt/tecsuite-parquet-out`)
- `INDEXER_PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST`: Host path to AbsTEC Parquet output
- `INDEXER_PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER`: Container path for AbsTEC Parquet (default: `/mnt/abstec-parquet-out`)

## Endpoints

All endpoints accept a `root` query parameter specifying the data directory path and return XML responses. If no `root` parameter is provided, the service uses the default container paths from environment variables.

### GET /health
Health check endpoint.
- Response: `{"status": "healthy"}`

### GET /rinex?root=/path/to/rinex/data
Returns RINEX server structure as XML.
- Supports both old (YYYY_original/DOY) and new (YYYY_original/MM/DD from 2019) layouts

### GET /tecsuite?root=/path/to/tecsuite/data
Returns TEC-suite DAT output structure as XML.

### GET /abstec?root=/path/to/abstec/data
Returns AbsTEC output structure as XML.

### GET /parquet?root=/path/to/parquet/data
Returns Parquet output structure as XML.

## XML Response Format

All endpoints return XML with the following structure:

```xml
<root_element>
  <item>
    <year>2025_original</year>
    <days>
      <item>
        <day>001</day>
        <stations>15</stations>
      </item>
      <item>
        <day>01/01</day>
        <stations>10</stations>
      </item>
    </days>
  </item>
</root_element>
```

## Setup

1. Copy `.env.example` to your main `.env` file or create a separate `.env` file
2. Configure the `INDEXER_*_PATH_HOST` variables to point to your actual data directories
3. The container paths are already set with sensible defaults

## Usage Examples

```bash
# Get RINEX structure (uses default path from env)
curl "http://localhost:5001/rinex"

# Get TEC-suite structure with custom path
curl "http://localhost:5001/tecsuite?root=/custom/path"

# Get AbsTEC structure (uses default path from env)
curl "http://localhost:5001/abstec"

# Get Parquet structure (uses default path from env)
curl "http://localhost:5001/parquet"
```

## Caching

The service uses in-memory caching based on directory modification time to avoid redundant filesystem scans.