from flask import Flask

from kbrd_api.config import Config
from kbrd_api.db import DB
from kbrd_api.api.hello import Hello
from kbrd_api.api.person import Person

def create_app() -> Flask:
    cfg = Config()
    app = Flask(__name__)

    db = DB(cfg.db_path)
    db.init_schema()

    Hello().register(app)
    Person(db).register(app)

    return app, cfg

def main() -> None:
    app, cfg = create_app()
    app.run(host=cfg.host, port=cfg.port)

if __name__ == "__main__":
    main()
