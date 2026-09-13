"""KBRD-API as it runs in development — `dev.sh` starts this.

A file of its own rather than a `python3 -c "..."` one-liner, and that is
the whole point of it: with `debug=True` Flask's reloader restarts the
process by re-executing it with the *same argv*, so a configuration living
in a `-c` string is frozen at launch. Editing the script then changes
nothing, however many times the reloader fires — the only way out being to
kill the process. Here the reloader watches this file like any other source
and a change to it actually takes effect.
"""

from pathlib import Path

from kbrd_api.config import Config
from kbrd_api.main import create_app

# Media and fonts live in KBRD-WEB's own `data/`, which mirrors the
# device's `/data`: the fonts are the ones its Makefile deploys to
# `/data/fonts`, and an upload lands in `data/media` the same way it lands
# in `/data/media` there. Anchored to this file rather than to the working
# directory, so where it's started from doesn't matter.
ROOT = Path(__file__).resolve().parent
WEB_DATA = ROOT.parent / "kbrd-web" / "data"


def main() -> None:
    config = Config(
        db_path=str(ROOT / "data" / "kbrd.db"),
        media_dir=str(WEB_DATA / "media"),
        font_dir=str(WEB_DATA / "fonts"),
        # Beside the development database rather than on `/data`,
        # which is the device's. Nothing here applies it either:
        # `/usr/bin/kbrd-network` only exists on the keyboard, so
        # `/api/network` answers `available: false` and KBRD-WEB
        # says so (see `api/network.py`).
        network_config_path=str(ROOT / "data" / "network.conf"),
    )
    app, config = create_app(config)
    print(f"  media : {config.media_dir}")
    print(f"  fonts : {config.font_dir}")
    app.run(host=config.host, port=config.port, debug=True)


if __name__ == "__main__":
    main()
