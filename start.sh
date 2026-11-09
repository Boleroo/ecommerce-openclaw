#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
PYTHON="$PROJECT_DIR/mock-api/venv/bin/python"
BACKEND_LOG="$PROJECT_DIR/mock-api/mock-api.log"
DISPATCHER_LOG="$PROJECT_DIR/dispatcher/dispatcher.log"
BACKEND_PID=""
DISPATCHER_PID=""

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing environment file: $ENV_FILE" >&2
  echo "Copy .env.example to .env and replace its placeholders." >&2
  exit 1
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing Python environment: $PYTHON" >&2
  exit 1
fi
if ! command -v openclaw >/dev/null 2>&1; then
  echo "openclaw is not installed or not available in PATH." >&2
  exit 1
fi

# Load values into this coordinator shell only. Each child receives a minimal,
# explicit environment below; human approval credentials never reach OpenClaw.
set +a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${API_TOKENS:?API_TOKENS must be set in .env}"
: "${OPENCLAW_ECOMMERCE_API_TOKEN:?OPENCLAW_ECOMMERCE_API_TOKEN must be set in .env}"

cleanup() {
  for pid in "$DISPATCHER_PID" "$BACKEND_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

backend_env=(
  "API_TOKENS=$API_TOKENS"
  "HUMAN_APPROVAL_TOKENS=${HUMAN_APPROVAL_TOKENS:-{}}"
  "LEGACY_READS=${LEGACY_READS:-false}"
)
if [[ -n "${DATABASE_URL:-}" ]]; then
  backend_env+=("DATABASE_URL=$DATABASE_URL")
fi

if curl -fsS --max-time 2 http://127.0.0.1:8080/healthz >/dev/null; then
  echo "Backend is already running on 127.0.0.1:8080"
else
  env "${backend_env[@]}" "$PYTHON" "$PROJECT_DIR/mock-api/app.py" >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  for _ in {1..20}; do
    curl -fsS --max-time 1 http://127.0.0.1:8080/healthz >/dev/null && break
    kill -0 "$BACKEND_PID" 2>/dev/null || { echo "Backend failed; see $BACKEND_LOG" >&2; exit 1; }
    sleep 0.25
  done
  curl -fsS --max-time 2 http://127.0.0.1:8080/healthz >/dev/null || { echo "Backend not healthy" >&2; exit 1; }
  echo "Backend ready (pid $BACKEND_PID)"
fi

if curl -fsS --max-time 2 http://127.0.0.1:8081/healthz >/dev/null; then
  echo "Dispatcher is already running on 127.0.0.1:8081"
else
  env \
    "DISPATCHER_BACKEND_URL=${DISPATCHER_BACKEND_URL:-http://127.0.0.1:8080}" \
    "DISPATCHER_AUDIT_DB=${DISPATCHER_AUDIT_DB:-$PROJECT_DIR/dispatcher/audit.sqlite3}" \
    "$PYTHON" -m dispatcher >"$DISPATCHER_LOG" 2>&1 &
  DISPATCHER_PID=$!
  for _ in {1..20}; do
    curl -fsS --max-time 1 http://127.0.0.1:8081/healthz >/dev/null && break
    kill -0 "$DISPATCHER_PID" 2>/dev/null || { echo "Dispatcher failed; see $DISPATCHER_LOG" >&2; exit 1; }
    sleep 0.25
  done
  curl -fsS --max-time 2 http://127.0.0.1:8081/healthz >/dev/null || { echo "Dispatcher not healthy" >&2; exit 1; }
  echo "Dispatcher ready (pid $DISPATCHER_PID)"
fi

echo "Starting OpenClaw with Agent-only Dispatcher credentials..."
env \
  -u API_TOKENS \
  -u HUMAN_APPROVAL_TOKENS \
  -u DISPATCHER_BACKEND_URL \
  -u DATABASE_URL \
  "OPENCLAW_ECOMMERCE_API_TOKEN=$OPENCLAW_ECOMMERCE_API_TOKEN" \
  "OPENCLAW_ECOMMERCE_DISPATCHER_URL=${OPENCLAW_ECOMMERCE_DISPATCHER_URL:-http://127.0.0.1:8081}" \
  "OPENCLAW_ECOMMERCE_TIMEOUT_MS=${OPENCLAW_ECOMMERCE_TIMEOUT_MS:-7000}" \
  openclaw gateway run
