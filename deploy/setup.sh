#!/usr/bin/env bash
# Lupus Automation — Oracle VM provisioning script.
#
# Run ONCE from the cloned repo on the server:
#   bash deploy/setup.sh
#
# What it does (safe to re-run — every step is idempotent):
#   1. Installs system packages (python3-venv, python3-pip, git)
#   2. Creates a Python virtual environment (if missing)
#   3. Installs pinned requirements
#   4. Creates .env from .env.example (if missing — never overwrites yours)
#   5. Installs the lupus-bot systemd service
#   6. Starts the service ONLY if BOT_TOKEN is already filled in .env
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
SERVICE_FILE="$REPO_DIR/deploy/lupus-bot.service"

echo "==> [1/6] Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git

echo "==> [2/6] Creating Python virtual environment"
if [ ! -d "$REPO_DIR/venv" ]; then
  python3 -m venv "$REPO_DIR/venv"
fi

echo "==> [3/6] Installing Python dependencies"
"$REPO_DIR/venv/bin/pip" install --upgrade pip -q
"$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt" -q

echo "==> [4/6] Creating .env from .env.example (if missing)"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/.env.example" "$ENV_FILE"
  echo "    .env created — EDIT IT with your real values before starting:"
  echo "    nano $ENV_FILE"
else
  echo "    .env already exists — leaving it untouched."
fi

echo "==> [5/6] Installing systemd service"
sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload

echo "==> [6/6] Checking .env readiness"
if grep -qE '^BOT_TOKEN=.+' "$ENV_FILE" 2>/dev/null; then
  echo "    BOT_TOKEN is set — enabling and starting the service."
  sudo systemctl enable --now lupus-bot
  sudo systemctl status lupus-bot --no-pager
else
  echo "    BOT_TOKEN is EMPTY — fill in $ENV_FILE first, then run:"
  echo "    sudo systemctl enable --now lupus-bot"
fi

echo "Done."
