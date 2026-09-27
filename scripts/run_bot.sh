#!/usr/bin/env bash
# Wrapper pour cron : exécute le bot UNE fois depuis le dossier du projet, puis s'arrête.
# Les arguments sont transmis au bot (ex. : --live).
set -euo pipefail
cd "$(dirname "$0")/.."
exec ./.venv/bin/python -m rsi2_bot "$@"
