#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Missing virtualenv at ${ROOT_DIR}/.venv" >&2
  exit 1
fi

export EXSCHOOL_HOST="${EXSCHOOL_HOST:-127.0.0.1}"
export EXSCHOOL_PORT="${EXSCHOOL_PORT:-8010}"
export EXSCHOOL_ROOT_PATH="${EXSCHOOL_ROOT_PATH-}"
if [[ -n "${EXSCHOOL_SESSION_SECRET:-}" ]]; then
  export EXSCHOOL_SESSION_SECRET
fi

cd "${ROOT_DIR}"
if [[ -n "${EXSCHOOL_ROOT_PATH}" ]]; then
  exec "${VENV_PYTHON}" -m uvicorn exschool_game.app:app \
    --host "${EXSCHOOL_HOST}" \
    --port "${EXSCHOOL_PORT}" \
    --proxy-headers \
    --root-path "${EXSCHOOL_ROOT_PATH}"
fi

exec "${VENV_PYTHON}" -m uvicorn exschool_game.app:app \
  --host "${EXSCHOOL_HOST}" \
  --port "${EXSCHOOL_PORT}" \
  --proxy-headers
