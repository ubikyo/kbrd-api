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
            # Renamed from `geometry`/`workspace`/`board`: a table that
            # already exists under an old name is renamed in place instead
            # of losing whatever it already held. SQLite rewrites any other
            # table's `REFERENCES` clause that pointed at the old name, so
            # this can safely run before anything below it is even aware
            # of the new names.
            existing_tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "geometry" in existing_tables and "layout" not in existing_tables:
                conn.execute("ALTER TABLE geometry RENAME TO layout")
                existing_tables.discard("geometry")
                existing_tables.add("layout")
            if "workspace" in existing_tables and "layer" not in existing_tables:
                conn.execute("ALTER TABLE workspace RENAME TO layer")
                existing_tables.discard("workspace")
                existing_tables.add("layer")
            if "board" in existing_tables and "display" not in existing_tables:
                conn.execute("ALTER TABLE board RENAME TO display")
                existing_tables.discard("board")
                existing_tables.add("display")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS layout (
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
            layout_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(layout)").fetchall()
            }
            if "active" not in layout_columns:
                conn.execute(
                    "ALTER TABLE layout ADD COLUMN active INTEGER NOT NULL DEFAULT 0"
                )
            # Settings › Geometry's Caps size / Gap size — per-layout, a
            # keycap's own size and the gap between two keycaps. Defaults
            # match KBRD-DEV's reference panel (see kbrd_web's own
            # `DEFAULT_LAYOUT_SETTINGS`), so a layout created before these
            # columns existed still gets sane values. (Physical width/height
            # used to live here too — see `display` below for why they
            # moved: they describe the physical screen, which doesn't
            # change when you switch layouts.)
            for column, default in (
                ("unit_mm", 19.05),
                ("gap_mm", 3),
            ):
                if column not in layout_columns:
                    conn.execute(
                        f"ALTER TABLE layout ADD COLUMN {column} "
                        f"REAL NOT NULL DEFAULT {default}"
                    )
            # How many 1U reference items the display fits, in each
            # direction — `NULL` (the default, including for a layout from
            # before these columns existed) means "as many as Caps size /
            # Gap size / the display's own physical size allow"; kbrd-web
            # clamps whatever's stored here to that same ceiling, so this
            # only ever narrows it, never widens it past what actually fits.
            for column in ("max_columns", "max_rows"):
                if column not in layout_columns:
                    conn.execute(
                        f"ALTER TABLE layout ADD COLUMN {column} REAL"
                    )
            conn.executescript("""
                -- The physical screen's own width/height — one value for
                -- the whole device, shared by every layout (unlike Caps
                -- size / Gap size on `layout`, which are per-layout).
                -- `id` is pinned to 1 so this table only ever holds the
                -- one row `Display.find`/`Display._write` read and update.
                CREATE TABLE IF NOT EXISTS display (
                  id                 INTEGER PRIMARY KEY CHECK (id = 1),
                  physical_width_mm  REAL NOT NULL DEFAULT 216,
                  physical_height_mm REAL NOT NULL DEFAULT 135
                );
            """)
            conn.execute(
                """
                INSERT OR IGNORE INTO display (id, physical_width_mm, physical_height_mm)
                VALUES (1, 216, 135)
                """
            )
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS layer (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  layout_id INTEGER NOT NULL REFERENCES layout(id) ON DELETE CASCADE,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  active INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS key_plugin (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  layer_id INTEGER NOT NULL REFERENCES layer(id) ON DELETE CASCADE,
                  key_ref TEXT NOT NULL,
                  plugin_id TEXT NOT NULL,
                  plugin_version TEXT NOT NULL,
                  position INTEGER NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  config TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS key_property (
                  layer_id INTEGER NOT NULL REFERENCES layer(id) ON DELETE CASCADE,
                  key_ref TEXT NOT NULL,
                  config TEXT NOT NULL DEFAULT '{}',
                  PRIMARY KEY(layer_id, key_ref)
                );
            """)
            # `layer` itself may just have been renamed from `workspace`
            # above, still holding its old `geometry_id`/`workspace_id`
            # column names — renamed in place so its existing rows survive.
            layer_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(layer)").fetchall()
            }
            if "geometry_id" in layer_columns and "layout_id" not in layer_columns:
                conn.execute("ALTER TABLE layer RENAME COLUMN geometry_id TO layout_id")
            if "factory_layout" not in layer_columns:
                conn.execute(
                    "ALTER TABLE layer ADD COLUMN factory_layout TEXT"
                )
            for table in ("key_plugin", "key_property"):
                columns = {
                    row["name"]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "workspace_id" in columns and "layer_id" not in columns:
                    conn.execute(
                        f"ALTER TABLE {table} RENAME COLUMN workspace_id TO layer_id"
                    )
            plugin_ids = {
                "kbrd.image": "kbrd.render-image",
                "kbrd.label": "kbrd.render-label",
                "kbrd.rectangle": "kbrd.render-rectangle",
                "kbrd.send-keys": "kbrd.invoke-keystroke",
                "kbrd.set-geometry": "kbrd.invoke-geometry",
                "kbrd.set-workspace": "kbrd.invoke-workspace",
                "kbrd.invoke-geometry": "kbrd.invoke-layout",
                "kbrd.invoke-workspace": "kbrd.invoke-layer",
            }
            conn.executemany(
                "UPDATE key_plugin SET plugin_id=? WHERE plugin_id=?",
                ((new_id, old_id) for old_id, new_id in plugin_ids.items()),
            )
            conn.commit()
