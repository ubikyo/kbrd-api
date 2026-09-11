#!/bin/sh

set -e

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

. .venv/bin/activate
pip install -e . flask

python3 dev_server.py
