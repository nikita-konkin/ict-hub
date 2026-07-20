# Data Indexer Service

A microservice that provides XML-structured data indexing for GNSS data processing.

## Environment Variables

The service uses the following environment variables (set in `.env` file):

### Cache Configuration
- `DATA_INDEXER_CACHE_TTL_SEC`: Cache time-to-live in seconds (default: `300.0` = 5 minutes)
  - Controls how often the service rescans directories for new files
  - Lower values = more frequent updates but higher CPU/disk usage
  - Higher values = less frequent updates but may miss new files sooner

### Filesystem Watchers (optional)
- `DATA_INDEXER_WATCHERS_ENABLED`: Enable filesystem watchers for fast cache invalidation (default: `true`)
  - If disabled, the indexer relies on cache TTL and/or explicit `refresh=true` requests.
  - On Linux, very large directory trees may hit the host `inotify` watch limit (`OSError: [Errno 28] inotify watch limit reached`).
    In that case either increase `fs.inotify.max_user_watches` on the host, or set `DATA_INDEXER_WATCHERS_ENABLED=false`.

### Large Dataset Tuning (optional)
- `DATA_INDEXER_MAX_YEARS`: Limit indexing to the newest N years (default: `0` = unlimited)
  - Useful when full scans take longer than `DATA_INDEXER_TIMEOUT_SEC` in ConverterHub.

### Persistent Cache Database
- `DATA_INDEXER_CACHE_DB_PATH`: Path to SQLite database for persistent caching (default: `/app/data/cache.db`)
  - Cache survives container restarts and rebuilds
  - Mount as a volume to persist across container lifecycles
  - Improves startup performance by avoiding re-indexing

### Startup Configuration
- `DATA_INDEXER_RUN_ON_STARTUP`: Control initial indexing behavior (default: `false`)
  - `false`: No initial indexing (default FastAPI behavior)
  - `true` or `async`: Run indexing asynchronously on FastAPI startup (non-blocking)
  - `sync`: Run indexing synchronously before starting FastAPI (blocking, requires Docker rebuild)

### Data Paths
Shared with converter-hub's own env vars of the same name (see `../.env.example`)
so both services always agree on where each data type is mounted.
- `RINEX_DATA_PATH_HOST` / `RINEX_DATA_PATH_CONTAINER` (container default: `/mnt/rinex-server`)
- `TECSUITE_OUT_DAT_DATA_PATH_HOST` / `TECSUITE_OUT_DAT_DATA_PATH_CONTAINER` (container default: `/mnt/tecsuite-out`)
- `ABSTEC_OUTPUT_DATA_PATH_HOST` / `ABSTEC_OUTPUT_DATA_PATH_CONTAINER` (container default: `/mnt/abstec-out`)
- `PARQUET_OUTPUT_TECSUITE_DATA_PATH_HOST` / `PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER` (container default: `/mnt/tecsuite-parquet-out`)
- `PARQUET_OUTPUT_ABSTEC_DATA_PATH_HOST` / `PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER` (container default: `/mnt/abstec-parquet-out`)

## Debug Logging

All indexing operations emit detailed debug logs to monitor which directories are being scanned in real-time.

### Log Tags
Each log message is tagged with a data-type prefix for easy filtering:
- `[RINEX]` — RINEX server structure scanning
- `[TEC-SUITE]` — TEC-suite DAT output scanning
- `[PARQUET]` — Parquet year/day structure scanning
- `[PARQUET-SAT]` — Parquet satellite/station extraction

### Log Levels
- **INFO**: Scan start/completion events and cache miss/expiration
  - `[TYPE] Cache expired/miss for {path} - starting full scan`
  - `Completed {TYPE} indexing for path: {path} - found {N} years`
- **DEBUG**: Detailed folder-by-folder traversal and file discovery
  - `[TYPE] Scanning year/day directory: {path}`
  - `[TYPE] Found {N} files/stations at {path}`

### Viewing Logs
View all indexing activity in real-time:
```bash
# Show all indexing debug messages
docker compose logs -f data-indexer | grep -E "\[RINEX\]|\[TEC-SUITE\]|\[PARQUET\]"

# Show only cache hit/miss events
docker compose logs -f data-indexer | grep -i "cache"

# Show complete logs for data-indexer
docker compose logs -f data-indexer
```

### Example Debug Output
```
[2026-04-15 12:34:56] [TEC-SUITE] Cache expired/miss for /mnt/tecsuite-out - starting full scan
[2026-04-15 12:34:56] [TEC-SUITE] Scanning year directory: /mnt/tecsuite-out/2025
[2026-04-15 12:34:56] [TEC-SUITE] Scanning day directory: /mnt/tecsuite-out/2025/001
[2026-04-15 12:34:56] [TEC-SUITE] Found site with .dat files: SITE001
[2026-04-15 12:34:56] [TEC-SUITE] Found site with .dat files: SITE002
[2026-04-15 12:34:57] Completed TEC-suite indexing for path: /mnt/tecsuite-out - found 1 years
[2026-04-15 12:35:01] [PARQUET-SAT] Cache HIT for /mnt/abstec-parquet-out (age: 5.1s, TTL: 300.0s)
```

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

### Forcing a refresh

All indexing endpoints accept an optional `refresh=true` query parameter to bypass cached results and rescan immediately:

```bash
curl "http://localhost:5001/tecsuite?refresh=true"
curl "http://localhost:5001/parquet-satellites?refresh=true"
```
