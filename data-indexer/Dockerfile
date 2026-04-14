FROM python:3.11-slim

# Optional build-time proxy support. These values are not persisted at runtime.
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make entrypoint script executable
RUN chmod +x entrypoint.sh

EXPOSE 5001

# Use entrypoint script if DATA_INDEXER_RUN_ON_STARTUP is set to 'sync', otherwise use direct uvicorn
CMD ["sh", "-c", "if [ \"${DATA_INDEXER_RUN_ON_STARTUP:-async}\" = \"sync\" ]; then ./entrypoint.sh; else uvicorn app:app --host 0.0.0.0 --port 5001; fi"]