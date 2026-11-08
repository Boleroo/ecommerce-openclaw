#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
MOCK_PYTHON="$PROJECT_DIR/mock-api/venv/bin/python"
MOCK_APP="$PROJECT_DIR/mock-api/app.py"
MOCK_LOG="$PROJECT_DIR/mock-api/mock-api.log"
MOCK_PID=""

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing environment file: $ENV_FILE" >&2
  exit 1
fi

if [[ ! -x "$MOCK_PYTHON" ]]; then
  echo "Missing Mock API virtual environment: $MOCK_PYTHON" >&2
  echo "Create it and install Flask before starting the project." >&2
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  echo "openclaw is not installed or not available in PATH." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${JD_API_ENDPOINT:?JD_API_ENDPOINT must be set in .env}"
: "${JD_SHOP_NAME:?JD_SHOP_NAME must be set in .env}"

cleanup() {
  if [[ -n "$MOCK_PID" ]] && kill -0 "$MOCK_PID" 2>/dev/null; then
    echo "Stopping Mock API (pid $MOCK_PID)..."
    kill "$MOCK_PID"
    wait "$MOCK_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if curl -fsS --max-time 2 "$JD_API_ENDPOINT/healthz" >/dev/null; then
  echo "Mock API is already running at $JD_API_ENDPOINT"
else
  echo "Starting Mock API at $JD_API_ENDPOINT..."
  "$MOCK_PYTHON" "$MOCK_APP" >"$MOCK_LOG" 2>&1 &
  MOCK_PID=$!

  for _ in {1..20}; do
    if curl -fsS --max-time 1 "$JD_API_ENDPOINT/healthz" >/dev/null; then
      break
    fi
    if ! kill -0 "$MOCK_PID" 2>/dev/null; then
      echo "Mock API exited during startup. See $MOCK_LOG" >&2
      exit 1
    fi
    sleep 0.25
  done

  if ! curl -fsS --max-time 2 "$JD_API_ENDPOINT/healthz" >/dev/null; then
    echo "Mock API did not become healthy. See $MOCK_LOG" >&2
    exit 1
  fi

  echo "Mock API is ready (pid $MOCK_PID)"
fi

echo "Starting OpenClaw Gateway on port 18789..."
openclaw gateway run
