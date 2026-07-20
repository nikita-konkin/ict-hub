#!/usr/bin/env bash
# Pull the latest (or a pinned) release from Docker Hub and restart the stack.
# Run from the Ubuntu server, inside the ict-hub/ checkout.
#
# Usage:
#   ./scripts/deploy.sh [tag]
#
# tag defaults to "latest". Requires .env to already exist (see .env.example)
# and `docker login` to have been run once on this host.
set -euo pipefail

TAG="${1:-latest}"

echo "==> pulling converter-hub + data-indexer (tag: ${TAG})"
IMAGE_TAG="${TAG}" docker compose pull converter-hub data-indexer

echo "==> pulling auxiliary images (tec-suite, dat-parquet-handler, abstec-suite)"
docker pull "nikitaikonkin/tec-suite:${TAG}"
docker pull "nikitaikonkin/dat-parquet-handler:${TAG}"
docker pull "nikitaikonkin/abstec-suite:${TAG}"

echo "==> restarting stack"
IMAGE_TAG="${TAG}" \
TECSUITE_IMAGE="nikitaikonkin/tec-suite:${TAG}" \
DAT_PARQUET_IMAGE="nikitaikonkin/dat-parquet-handler:${TAG}" \
ABSTEC_SUITE_IMAGE="nikitaikonkin/abstec-suite:${TAG}" \
docker compose up -d

echo "==> pruning dangling images"
docker image prune -f

echo "==> done"
docker compose ps
