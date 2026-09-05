#!/bin/sh

set -e

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

. .venv/bin/activate
pip install -e . flask

python3 -c "
from kbrd_api.config import Config
from kbrd_api.main import create_app

cfg = Config(db_path='data/kbrd.db', media_dir='data/media', font_dir='data/fonts')
app, cfg = create_app(cfg)
app.run(host=cfg.host, port=cfg.port, debug=True)
"
