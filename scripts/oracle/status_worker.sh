#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="alphapulse-worker"
RELAY_URL="${CLOUDFLARE_RELAY_URL:-https://lumetrix55active-neonorbit.onerfaber.workers.dev}"

printf '\n=== systemd ===\n'
systemctl --no-pager --full status "${SERVICE_NAME}.service" || true

printf '\n=== local gateway ===\n'
curl -fsS --max-time 3 http://127.0.0.1:8765/health || true
printf '\n'

printf '\n=== Cloudflare bridge ===\n'
curl -fsS --max-time 5 "${RELAY_URL}/__bridge/health" || true
printf '\n'

printf '\n=== recent logs ===\n'
journalctl -u "${SERVICE_NAME}.service" -n 60 --no-pager
