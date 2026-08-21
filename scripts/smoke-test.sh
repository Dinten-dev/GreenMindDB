#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# GreenMind — Smoke Test
# Usage: ./scripts/smoke-test.sh <url>
# Example: ./scripts/smoke-test.sh https://test.green-mind.ch
# ─────────────────────────────────────────────────────────

if [[ -z "${1:-}" ]]; then
    echo "Usage: $0 <base_url>"
    exit 1
fi

BASE_URL="${1%/}"
echo "🔍 Running smoke tests against $BASE_URL..."

# 1. Frontend reachability. Follow the locale redirect, but keep TLS
# verification, connection timeouts, and a small redirect limit enabled.
echo "  [1/2] Checking Frontend (GET /) ..."
FRONTEND_STATUS=$(curl --silent --show-error --location --max-redirs 3 \
    --connect-timeout 5 --max-time 15 -o /dev/null -w "%{http_code}\n" "$BASE_URL/")
if [[ "$FRONTEND_STATUS" == "200" ]]; then
    echo "  ✅ Frontend OK ($FRONTEND_STATUS)"
else
    echo "  ❌ Frontend failing (HTTP $FRONTEND_STATUS)"
    exit 1
fi

# 2. Backend health check through the public proxy.
echo "  [2/2] Checking Backend via proxy (GET /health) ..."
BACKEND_STATUS=$(curl --silent --show-error --connect-timeout 5 --max-time 15 \
    -o /dev/null -w "%{http_code}\n" "$BASE_URL/health")
if [[ "$BACKEND_STATUS" == "200" ]]; then
    echo "  ✅ Backend OK ($BACKEND_STATUS)"
else
    echo "  ❌ Backend failing (HTTP $BACKEND_STATUS)"
    exit 1
fi

echo "🎉 All smoke tests passed for $BASE_URL!"
exit 0
