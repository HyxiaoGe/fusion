#!/usr/bin/env bash
# Per-boot startup: bring up local infra (Redis + Postgres) and apply DB migrations.
# The Fusion app servers (uvicorn / next dev) are intentionally NOT auto-started,
# matching the repository convention of not launching Fusion services implicitly.
# Start them on demand:
#   backend : cd backend  && source .venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000
#   frontend: cd frontend && npm run dev:next
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[start] Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  redis-server --daemonize yes --save '' --appendonly no
fi

echo "[start] Postgres"
PG_VERSION="$(ls /etc/postgresql 2>/dev/null | sort -V | tail -1 || true)"
if ! pg_isready -h localhost -q 2>/dev/null; then
  sudo pg_ctlcluster "${PG_VERSION:-16}" main start || true
fi
for _ in $(seq 1 30); do
  pg_isready -h localhost -q 2>/dev/null && break
  sleep 1
done

echo "[start] Ensure fusion role + database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='fusion'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE fusion LOGIN PASSWORD 'fusion123!!';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='fusion'" | grep -q 1 \
  || sudo -u postgres createdb -O fusion fusion

echo "[start] Apply database migrations"
cd "$ROOT/backend"
# shellcheck disable=SC1091
source .venv/bin/activate
alembic upgrade head
deactivate

echo "[start] Ready (Redis + Postgres up, migrations applied)"
