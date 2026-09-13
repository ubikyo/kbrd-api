from flask import Flask

from kbrd_api.config import Config
from kbrd_api.db import DB
from kbrd_api.api.docs import Docs
from kbrd_api.api.health import Health
from kbrd_api.api.display import Display
from kbrd_api.api.layout import Layout
from kbrd_api.api.layer import Layer
from kbrd_api.api.media_category import MediaCategory
from kbrd_api.api.agent import Agent
from kbrd_api.api.device import Device
from kbrd_api.api.backup import Backup
from kbrd_api.api.storage import Storage


def create_app(cfg: Config | None = None) -> tuple[Flask, Config]:
    cfg = cfg or Config()
    app = Flask(__name__)
    # 200 MiB, the same ceiling nginx puts on a request body in front of
    # this service (`client_max_body_size` in `kbrd-web.conf`) and the
    # same one KBRD-WEB turns a file away at before sending it. The three
    # have to stay in step: a body refused by nginx never reaches Flask,
    # so a lower value here would only ever produce a less useful error.
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

    db = DB(cfg.db_path)
    db.init_schema()

    Health().register(app)
    Docs().register(app)
    Agent().register(app)
    Device().register(app)
    Storage().register(app)
    Backup(db, cfg.media_dir).register(app)
    Display(db).register(app)
    layout = Layout(db)
    layout.register(app)
    layer = Layer(
        db,
        layout,
        cfg.media_dir,
        cfg.font_dir,
        cfg.bundled_font_dir,
    )
    layer.register(app)
    MediaCategory(db, layer).register(app)

    return app, cfg

def main() -> None:
    app, cfg = create_app()
    app.run(host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
