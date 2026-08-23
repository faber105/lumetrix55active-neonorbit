#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/alphapulse}"
BRANCH="${BRANCH:-codex-windows-worker-rebuild}"
ENV_FILE="${ENV_FILE:-/etc/alphapulse/worker.env}"
SERVICE_NAME="alphapulse-worker"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/oracle/update_worker.sh" >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}" >&2
  exit 2
fi
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  echo "Worker is not installed in ${APP_DIR}" >&2
  exit 3
fi

# Refuse a restart while a trading session or execution is unresolved.
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
"${APP_DIR}/.venv/bin/python" - <<'PY'
import asyncio, os
import asyncpg

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        active = await conn.fetchval("SELECT COUNT(*) FROM auto_trade_sessions WHERE status='ACTIVE'")
        positions = await conn.fetchval("SELECT COUNT(*) FROM auto_trade_legs WHERE result IN ('PENDING','UNKNOWN')")
        executions = await conn.fetchval("SELECT COUNT(*) FROM trade_executions WHERE status IN ('EXECUTING','UNKNOWN')")
    finally:
        await conn.close()
    print(f"active_sessions={active} unresolved_positions={positions} unresolved_executions={executions}")
    if any((active, positions, executions)):
        raise SystemExit("Unsafe to update: finish/stop the active trading state first.")

asyncio.run(main())
PY

systemctl stop "${SERVICE_NAME}.service"
git -C "${APP_DIR}" fetch origin "${BRANCH}"
git -C "${APP_DIR}" checkout "${BRANCH}"
git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
"${APP_DIR}/.venv/bin/pip" install --no-cache-dir -r "${APP_DIR}/requirements.txt"
systemctl start "${SERVICE_NAME}.service"
sleep 5
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
