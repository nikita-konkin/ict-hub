#!/usr/bin/env bash
# Build and push all 6 ict-hub images to Docker Hub (nikitaikonkin/*).
# Run from the work PC after testing locally with `docker compose up --build`.
#
# Usage:
#   ./scripts/release.sh [tag]
#
# tag defaults to "latest". Pass a version (e.g. v0.3) to also push a
# versioned tag alongside latest for every image.
set -euo pipefail

TAG="${1:-latest}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> docker login (skip if already logged in)"
docker login

tag_and_push_version() {
  local name="$1"
  if [[ "${TAG}" != "latest" ]]; then
    docker tag "nikitaikonkin/${name}:latest" "nikitaikonkin/${name}:${TAG}"
    docker push "nikitaikonkin/${name}:${TAG}"
  fi
}

echo "==> building converter-hub + data-indexer"
(cd "${ROOT}/ict-hub" && docker compose build converter-hub data-indexer && docker compose push converter-hub data-indexer)
tag_and_push_version "ict-hub-converter"
tag_and_push_version "ict-hub-data-indexer"

for pair in "tec-suite:tec-suite" "dat-parquet-handler:dat-parquet-handler" "abstec-suite:abstec-suite" "tec-stat:tec-backend"; do
  dir="${pair%%:*}"
  name="${pair##*:}"
  echo "==> building ${name}"
  (cd "${ROOT}/${dir}" && docker compose build && docker compose push)
  tag_and_push_version "${name}"
done

echo "==> done. On the server run: ./scripts/deploy.sh ${TAG}"
