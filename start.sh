#!/usr/bin/env bash
# pm2 entrypoint for gh-scrapeforge — loads .env and execs uvicorn from the venv.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
set -a
# shellcheck disable=SC1091
source "$DIR/.env"
set +a
export PYTHONPATH="$DIR"
exec "$DIR/.venv/bin/python" -m uvicorn api.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-4222}" --workers "${WORKERS:-1}"
