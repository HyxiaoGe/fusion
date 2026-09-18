#!/usr/bin/env bash
# Idempotent dependency bootstrap for the Fusion monorepo (backend + frontend).
# Runs after the repository is checked out. Must terminate and be safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[install] Ensuring system packages (postgres, redis, python venv)"
if ! command -v redis-server >/dev/null 2>&1 \
  || ! command -v pg_ctlcluster >/dev/null 2>&1 \
  || ! dpkg -s python3.12-venv >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3.12-venv \
    postgresql \
    postgresql-contrib \
    redis-server \
    libpq-dev
fi

echo "[install] Backend: Python virtualenv + dependencies"
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3.12 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements-dev.txt
if [ ! -f .env ]; then
  echo "[install] Backend: creating .env from .env.example (local Postgres/Redis)"
  cp .env.example .env
  sed -i 's#^REDIS_URL=.*#REDIS_URL=redis://localhost:6379/0#' .env
  CEK="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  sed -i "s#^CREDENTIAL_ENCRYPTION_KEY=.*#CREDENTIAL_ENCRYPTION_KEY=${CEK}#" .env
fi
deactivate

echo "[install] Frontend: npm dependencies"
cd "$ROOT/frontend"
export ELECTRON_SKIP_BINARY_DOWNLOAD=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
npm ci --no-audit --no-fund
if [ ! -f .env.local ]; then
  echo "[install] Frontend: creating .env.local from .env.example"
  cp .env.example .env.local
fi

echo "[install] Done"
