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
                  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                  name               TEXT NOT NULL,
                  description        TEXT NOT NULL DEFAULT '',
                  author             TEXT NOT NULL DEFAULT '',
                  unit               TEXT NOT NULL CHECK(unit IN ('px', 'mm')),
                  geometry           TEXT NOT NULL,
                  svg                TEXT NOT NULL DEFAULT '',
                  active             INTEGER NOT NULL DEFAULT 0,
                  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                  unit_mm            REAL NOT NULL DEFAULT 19.05,
                  gap_mm             REAL NOT NULL DEFAULT 3,
                  max_columns        REAL,
                  max_rows           REAL
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
            # Settings › Geometry's Caps size / Gap size — per-layout, a
            # keycap's own size and the gap between two keycaps. Defaults
            # match KBRD-DEV's reference panel (see kbrd_web's own
            # `DEFAULT_LAYOUT_SETTINGS`), so a geometry created before
            # these columns existed still gets sane values. (Physical
            # width/height used to live here too — see `board` below for
            # why they moved: they describe the physical screen, which
            # doesn't change when you switch layouts.)
            for column, default in (
                ("unit_mm", 19.05),
                ("gap_mm", 3),
            ):
                if column not in geometry_columns:
                    conn.execute(
                        f"ALTER TABLE geometry ADD COLUMN {column} "
                        f"REAL NOT NULL DEFAULT {default}"
                    )
            # How many 1U reference items the board fits, in each
            # direction — `NULL` (the default, including for a geometry
            # from before these columns existed) means "as many as Caps
            # size / Gap size / the board's own physical size allow";
            # kbrd-web clamps whatever's stored here to that same ceiling,
            # so this only ever narrows it, never widens it past what
            # actually fits.
            for column in ("max_columns", "max_rows"):
                if column not in geometry_columns:
                    conn.execute(
                        f"ALTER TABLE geometry ADD COLUMN {column} REAL"
                    )
            conn.executescript("""
                -- The physical screen's own width/height — one value for
                -- the whole device, shared by every layout (unlike Caps
                -- size / Gap size on `geometry`, which are per-layout).
                -- `id` is pinned to 1 so this table only ever holds the
                -- one row `Board.find`/`Board._write` read and update.
                CREATE TABLE IF NOT EXISTS board (
                  id                 INTEGER PRIMARY KEY CHECK (id = 1),
                  physical_width_mm  REAL NOT NULL DEFAULT 216,
                  physical_height_mm REAL NOT NULL DEFAULT 135
                );
            """)
            conn.execute(
                """
                INSERT OR IGNORE INTO board (id, physical_width_mm, physical_height_mm)
                VALUES (1, 216, 135)
                """
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
            workspace_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(workspace)").fetchall()
            }
            if "factory_layout" not in workspace_columns:
                conn.execute(
                    "ALTER TABLE workspace ADD COLUMN factory_layout TEXT"
                )
            plugin_ids = {
                "kbrd.image": "kbrd.render-image",
                "kbrd.label": "kbrd.render-label",
                "kbrd.rectangle": "kbrd.render-rectangle",
                "kbrd.send-keys": "kbrd.invoke-keystroke",
                "kbrd.set-geometry": "kbrd.invoke-geometry",
                "kbrd.set-workspace": "kbrd.invoke-workspace",
            }
            conn.executemany(
                "UPDATE key_plugin SET plugin_id=? WHERE plugin_id=?",
                ((new_id, old_id) for old_id, new_id in plugin_ids.items()),
            )
            conn.commit()
