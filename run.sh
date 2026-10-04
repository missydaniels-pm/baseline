#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Match production's Python (the Dockerfile's base image). Homebrew installs
# python@3.14 without linking `python3.14` onto PATH when another Python owns
# the name, so fall back to its keg path.
PY=$(command -v python3.14 || true)
if [ -z "$PY" ] && command -v brew >/dev/null; then
  PY="$(brew --prefix python@3.14 2>/dev/null || true)/bin/python3.14"
fi
if [ ! -x "$PY" ]; then
  echo "Python 3.14 not found — install it with: brew install python@3.14" >&2
  exit 1
fi

# Rebuild the venv if it was made with a different Python (e.g. the old 3.10).
if [ -d ".venv" ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 14))' 2>/dev/null; then
  echo "Existing .venv is not Python 3.14 — recreating it..."
  rm -rf .venv
fi
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (Python 3.14)..."
  "$PY" -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip3 install -q -r requirements.txt

# app.debug now comes from this, not a hardcoded debug=True in app.py. Without
# it the local /dev/* routes are gated off exactly as they are in production.
export DEBUG=true

echo "Starting Baseline at http://localhost:${PORT:-5001}"
python app.py
