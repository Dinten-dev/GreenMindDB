#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python3 prepare-direct-views.py
docker compose -f compose.yml -f direct-views.yml config -q
# Additive tables only, in the isolated Direct database.
docker compose -f compose.yml -f direct-views.yml run --rm --no-deps -T visual-direct-worker python -m app.visualization.direct init
docker compose -f compose.yml -f direct-views.yml up -d --no-deps visual-direct-worker visual-api visual-frontend
