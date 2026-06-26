#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if command -v apt-get >/dev/null 2>&1; then
  echo "Debian/Ubuntu system packages:"
  echo "sudo apt-get update && sudo apt-get install -y ffmpeg fontconfig fonts-hosny-amiri fonts-liberation2 fonts-open-sans libfribidi0 libharfbuzz0b libass9 redis-server unzip"
elif command -v pacman >/dev/null 2>&1; then
  echo "Arch system packages:"
  echo "sudo pacman -S --needed --noconfirm ffmpeg fontconfig ttf-amiri ttf-liberation redis unzip"
elif command -v brew >/dev/null 2>&1; then
  echo "macOS packages:"
  echo "brew install ffmpeg fontconfig redis && brew install --cask font-amiri font-liberation"
fi

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if [ -x "$HOME/.pyenv/versions/3.12.11/bin/python" ]; then
    PYTHON_BIN="$HOME/.pyenv/versions/3.12.11/bin/python"
  else
    echo "Python 3.12 is required" >&2
    exit 1
  fi
fi

uv venv --python "$PYTHON_BIN" .venv
. .venv/bin/activate
uv pip install -e ".[dev]"
npm ci --prefix apps/web
echo "bootstrap complete"
