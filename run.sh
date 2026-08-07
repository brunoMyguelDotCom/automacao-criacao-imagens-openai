#!/usr/bin/env bash
# ============================================================
# GPT Image Batch Generator — script de execução (Linux/macOS)
# OBS: o alvo é ChatGPT Desktop (Windows). Use run.bat no Windows.
# ============================================================

set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERRO] python3 nao encontrado."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Criando ambiente virtual..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python main.py "$@"
