from flask import Flask

from kbrd_api.config import Config
from kbrd_api.db import DB
from kbrd_api.api.health import Health
from kbrd_api.api.geometry import Geometry
from kbrd_api.api.workspace import Workspace


def create_app(cfg: Config | None = None) -> tuple[Flask, Config]:
    cfg = cfg or Config()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

    db = DB(cfg.db_path)
    db.init_schema()

    Health().register(app)
    geometry = Geometry(db)
    geometry.register(app)
    Workspace(
        db,
        geometry,
        cfg.media_dir,
        cfg.font_dir,
        cfg.bundled_font_dir,
    ).register(app)

    return app, cfg

def main() -> None:
    app, cfg = create_app()
    app.run(host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
