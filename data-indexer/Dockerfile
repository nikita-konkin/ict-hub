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

# entrypoint.sh handles DATA_INDEXER_RUN_ON_STARTUP (false | true/async | sync)
# and then execs uvicorn.
CMD ["./entrypoint.sh"]