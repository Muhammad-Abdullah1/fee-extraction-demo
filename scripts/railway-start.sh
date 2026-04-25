#!/bin/sh
set -eu

PORT="${PORT:-8000}"
FEES_DB_PATH="${FEES_DB_PATH:-/app/data/fees.db}"
export FEES_DB_PATH

DB_DIR="$(dirname "$FEES_DB_PATH")"
mkdir -p "$DB_DIR"

if [ ! -f "$FEES_DB_PATH" ]; then
  echo "No database found at $FEES_DB_PATH. Bootstrapping from bundled fixtures..."
  python -m scripts.run_extraction --reset
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
