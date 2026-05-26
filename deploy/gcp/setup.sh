#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo."
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/enzo9355/okx-trading-bot.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/okx-trading-bot}"
SERVICE_USER="${SERVICE_USER:-okxbot}"
SERVICE_NAME="${SERVICE_NAME:-okx-bot}"
BOT_MODE="${BOT_MODE:-both}"
ENV_DIR="${ENV_DIR:-/etc/okx-trading-bot}"
ENV_FILE="${ENV_FILE:-${ENV_DIR}/okx-bot.env}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

case "${BOT_MODE}" in
  spot|futures|both) ;;
  *)
    echo "BOT_MODE must be one of: spot, futures, both."
    exit 1
    ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv python3-pip ca-certificates

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin "${BRANCH}"
  git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"
else
  rm -rf "${APP_DIR}"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

install -d -m 0750 -o root -g "${SERVICE_USER}" "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  install -m 0640 -o root -g "${SERVICE_USER}" "${APP_DIR}/.env.example" "${ENV_FILE}"
  sed -i 's/^SANDBOX_MODE=.*/SANDBOX_MODE=true/' "${ENV_FILE}"
  sed -i 's/^DRY_RUN=.*/DRY_RUN=true/' "${ENV_FILE}"
fi

install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_DIR}/data" "${APP_DIR}/logs"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=OKX Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/main.py --mode ${BOT_MODE}
Restart=always
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=${APP_DIR}/data ${APP_DIR}/logs

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo "Installed ${SERVICE_NAME}."
echo "Edit secrets before starting: sudo nano ${ENV_FILE}"
echo "Start service: sudo systemctl start ${SERVICE_NAME}"
echo "View logs: sudo journalctl -u ${SERVICE_NAME} -f"
