#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip3 install -q -r requirements.txt

# app.debug now comes from this, not a hardcoded debug=True in app.py. Without
# it the local /dev/* routes are gated off exactly as they are in production.
export DEBUG=true

echo "Starting Baseline at http://localhost:5001"
python app.py
