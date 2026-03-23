#!/bin/bash
set -e

# Run tests in Docker explicitly
echo "🚀 Spinning up test stack and running tests..."
docker compose -f docker-compose.test.yml up --build --exit-code-from test_runner || {
  echo "❌ Tests failed."
  docker compose -f docker-compose.test.yml down -v
  exit 1
}

echo "✅ All tests passed!"
docker compose -f docker-compose.test.yml down -v
