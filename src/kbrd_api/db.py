import os
import sqlite3


class Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class DB:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.db_path, factory=Connection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
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
                  active      INTEGER NOT NULL DEFAULT 0,
                  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)
            geometry_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(geometry)").fetchall()
            }
            if "active" not in geometry_columns:
                conn.execute(
                    "ALTER TABLE geometry ADD COLUMN active INTEGER NOT NULL DEFAULT 0"
                )
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workspace (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  geometry_id INTEGER NOT NULL REFERENCES geometry(id) ON DELETE CASCADE,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  active INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS key_plugin (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  workspace_id INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                  key_ref TEXT NOT NULL,
                  plugin_id TEXT NOT NULL,
                  plugin_version TEXT NOT NULL,
                  position INTEGER NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  config TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS key_property (
                  workspace_id INTEGER NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
                  key_ref TEXT NOT NULL,
                  config TEXT NOT NULL DEFAULT '{}',
                  PRIMARY KEY(workspace_id, key_ref)
                );
            """)
            conn.commit()
