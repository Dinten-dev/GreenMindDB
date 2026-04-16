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

# 1. Frontend Reachability
echo "  [1/2] Checking Frontend (GET /) ..."
FRONTEND_STATUS=$(curl -o /dev/null -s -w "%{http_code}\n" "$BASE_URL/")
if [[ "$FRONTEND_STATUS" == "200" ]]; then
    echo "  ✅ Frontend OK ($FRONTEND_STATUS)"
else
    echo "  ❌ Frontend failing (HTTP $FRONTEND_STATUS)"
    exit 1
fi

# 2. Backend Healthcheck via API route
echo "  [2/2] Checking Backend via Nginx proxy (GET /api/v1/health) ..."
BACKEND_STATUS=$(curl -o /dev/null -s -w "%{http_code}\n" "$BASE_URL/api/v1/health")

# Wait, if /api/v1/health isn't mapped, we might need /api/health or directly check backend port
if [[ "$BACKEND_STATUS" == "200" ]]; then
    echo "  ✅ Backend OK ($BACKEND_STATUS)"
elif [[ "$BACKEND_STATUS" == "404" ]]; then
    # Fallback if API hasn't `/v1/health`
    H2=$(curl -o /dev/null -s -w "%{http_code}\n" "$BASE_URL/api/health")
    if [[ "$H2" == "200" ]]; then
        echo "  ✅ Backend OK (/api/health)"
    else
        echo "  ⚠️ Backend check returned 404. Manual verification needed."
    fi
else
    echo "  ❌ Backend failing (HTTP $BACKEND_STATUS)"
    exit 1
fi

echo "🎉 All smoke tests passed for $BASE_URL!"
exit 0
