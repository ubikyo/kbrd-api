import os
import sqlite3


class DB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS geometry (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  name        TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  author      TEXT NOT NULL DEFAULT '',
                  unit        TEXT NOT NULL CHECK(unit IN ('px', 'mm')),
                  geometry    TEXT NOT NULL,
                  svg         TEXT NOT NULL DEFAULT '',
                  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
            conn.commit()
