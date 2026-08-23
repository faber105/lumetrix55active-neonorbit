#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/alphapulse}"
BRANCH="${BRANCH:-codex-windows-worker-rebuild}"
REPO_URL="${REPO_URL:-https://github.com/faber105/lumetrix55active-neonorbit.git}"
ENV_FILE="${ENV_FILE:-/etc/alphapulse/worker.env}"
SERVICE_NAME="alphapulse-worker"
RUN_USER="${RUN_USER:-opc}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/oracle/install_worker.sh" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy the existing worker.env to the server first." >&2
  exit 2
fi

# Runtime + build dependencies for Python/ARM64 wheels.
dnf install -y git python3 python3-pip python3-devel gcc gcc-c++ make openssl-devel libffi-devel

mkdir -p "$(dirname "${ENV_FILE}")" "${APP_DIR}"
chmod 700 "$(dirname "${ENV_FILE}")"
chmod 600 "${ENV_FILE}"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  rm -rf "${APP_DIR}"
  git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" checkout "${BRANCH}"
  git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"${APP_DIR}/.venv/bin/pip" install --no-cache-dir -r "${APP_DIR}/requirements.txt"

chown -R "${RUN_USER}:${RUN_USER}" "${APP_DIR}"

cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=AlphaPulse persistent Oracle worker
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=10

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=APP_RUNTIME_ROLE=worker
Environment=AUTO_REALTIME_DRIVER=true
Environment=WORKER_ID=alphapulse-oracle-milan-1
Environment=WORKER_HTTP_PORT=8765
Environment=CLOUDFLARE_RELAY_URL=https://lumetrix55active-neonorbit.onerfaber.workers.dev
Environment=MINI_APP_URL=https://lumetrix55active-neonorbit.onerfaber.workers.dev
ExecStart=${APP_DIR}/.venv/bin/python -m worker.main
Restart=always
RestartSec=3
TimeoutStopSec=30
KillSignal=SIGTERM
LimitNOFILE=65535
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

echo "Installed ${SERVICE_NAME}.service"
echo "Do not start it until the old Windows worker is stopped and its lease has expired."
echo "Then run: sudo systemctl start ${SERVICE_NAME}"
