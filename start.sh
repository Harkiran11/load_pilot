#!/usr/bin/env bash
# Start LoadPilot with Docker. Everything is local; nothing is sent anywhere.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop, then run this script again." >&2
  exit 1
fi

docker compose up --build -d --wait --wait-timeout 300

port="${FRONTEND_PORT:-5173}"
echo
echo "LoadPilot is ready at http://localhost:${port}"
echo "API documentation:      http://localhost:${BACKEND_PORT:-8000}/docs"
echo "Stop with:              docker compose down"
