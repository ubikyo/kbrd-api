import json
from dataclasses import asdict

from flask import Flask, jsonify, request

from kbrd_api.db import DB
from .geometry_layout import layout_geometry
from .geometry_svg import render_geometry_svg


LAYOUT_COLUMNS = """
    id, name, description, author, unit, geometry, svg, active, created_at,
    unit_mm, gap_mm, max_columns, max_rows
"""

# Matches kbrd-web's own `DEFAULT_LAYOUT_SETTINGS` — used when a client
# doesn't send one of these fields (e.g. a plain rename via the Layout
# editor, which only ever touches name/description/author).
DEFAULT_UNIT_MM = 19.05
DEFAULT_GAP_MM = 3

DEFAULT_LAYOUT_ORDER = """
    ORDER BY
        active DESC,
        CASE WHEN lower(name) = 'default' THEN 0 ELSE 1 END,
        name,
        id
"""


class Layout:
    def __init__(self, db: DB):
        self.db = db

    @staticmethod
    def row_to_dict(row) -> dict:
        geometry = json.loads(row["geometry"])
        layout = layout_geometry(geometry)
        result = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "author": row["author"],
            "unit": row["unit"],
            "geometry": geometry,
            "svg": render_geometry_svg(layout, row["unit"]),
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "layout": asdict(layout),
            # Settings › Geometry's Caps size / Gap size (see kbrd-web's
            # `LayoutSettings`) — opaque numbers to KBRD-API, just stored
            # and returned as-is so they survive a reload / a switch back
            # to this layout. The physical screen's width/height live on
            # `display` instead — see its own comment in `db.py`.
            "unit_mm": row["unit_mm"],
            "gap_mm": row["gap_mm"],
            # How many 1U reference items fit, in each direction — `null`
            # means "as many as fit" (kbrd-web computes and clamps to that
            # ceiling itself); see the column's own comment in `db.py`.
            "max_columns": row["max_columns"],
            "max_rows": row["max_rows"],
        }
        return result

    @staticmethod
    def _positive_number(data, key: str, default: float) -> float:
        value = data.get(key, default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if number < 0:
            raise ValueError(f"{key} must not be negative")
        return number

    @staticmethod
    def _optional_positive_number(data, key: str):
        # Max width/height (1U) — kbrd-web's own NumberInput steps these by
        # 0.25 (like a cell's own Unit, see `MIN_UNIT`/`UNIT_STEP`), not by
        # a whole 1U item at a time, so this accepts either int or float —
        # only `null` (the default: "as many as fit") and a value below the
        # 1-item floor are rejected.
        if key not in data or data[key] is None:
            return None
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number or null")
        if value < 1:
            raise ValueError(f"{key} must be at least 1")
        return float(value)

    @staticmethod
    def _payload(data) -> dict:
        if not isinstance(data, dict):
            raise ValueError("body must be an object")

        name = str(data.get("name") or "").strip()
        unit = str(data.get("unit") or "").strip()
        if not name:
            raise ValueError("missing name")
        if unit not in ("px", "mm"):
            raise ValueError("unit must be 'px' or 'mm'")

        geometry = data.get("geometry")
        layout = layout_geometry(geometry)

        return {
            "name": name,
            "description": str(data.get("description") or "").strip(),
            "author": str(data.get("author") or "").strip(),
            "unit": unit,
            "unit_mm": Layout._positive_number(
                data, "unit_mm", DEFAULT_UNIT_MM
            ),
            "gap_mm": Layout._positive_number(
                data, "gap_mm", DEFAULT_GAP_MM
            ),
            "max_columns": Layout._optional_positive_number(data, "max_columns"),
            "max_rows": Layout._optional_positive_number(data, "max_rows"),
            "geometry": json.dumps(
                geometry,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "svg": render_geometry_svg(layout, unit),
        }

    @staticmethod
    def find(conn, layout_id: int):
        return conn.execute(
            f"SELECT {LAYOUT_COLUMNS} FROM layout WHERE id=?",
            (layout_id,),
        ).fetchone()

    @staticmethod
    def find_default(conn):
        """Layout used when no layout is explicitly active: the active one
        if any, otherwise the layout named 'default', otherwise the first
        one alphabetically."""
        return conn.execute(
            f"SELECT {LAYOUT_COLUMNS} FROM layout {DEFAULT_LAYOUT_ORDER} LIMIT 1"
        ).fetchone()

    def _write(self, layout_id: int | None = None):
        try:
            payload = self._payload(request.get_json(silent=True))
        except (TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400

        fields = (
            payload["name"],
            payload["description"],
            payload["author"],
            payload["unit"],
            payload["geometry"],
            payload["svg"],
            payload["unit_mm"],
            payload["gap_mm"],
            payload["max_columns"],
            payload["max_rows"],
        )
        with self.db.connect() as conn:
            if layout_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO layout (
                        name, description, author, unit, geometry, svg,
                        unit_mm, gap_mm, max_columns, max_rows
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    fields,
                )
                layout_id = cursor.lastrowid
                status = 201
            else:
                cursor = conn.execute(
                    """
                    UPDATE layout
                    SET name=?, description=?, author=?, unit=?, geometry=?, svg=?,
                        unit_mm=?, gap_mm=?, max_columns=?, max_rows=?
                    WHERE id=?
                    """,
                    (*fields, layout_id),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                status = 200

            conn.commit()
            row = self.find(conn, layout_id)
            return jsonify(self.row_to_dict(row)), status

    def register(self, app: Flask) -> None:
        @app.get("/api/layout")
        def list_layouts():
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"SELECT {LAYOUT_COLUMNS} FROM layout ORDER BY name, id"
                ).fetchall()
                return jsonify([self.row_to_dict(row) for row in rows])

        @app.get("/api/layout/active")
        def get_active_layout():
            with self.db.connect() as conn:
                row = self.find_default(conn)
                if row is None:
                    return jsonify(error="not found"), 404
                return jsonify(self.row_to_dict(row))

        @app.put("/api/layout/<int:layout_id>/activate")
        def activate_layout(layout_id: int):
            with self.db.connect() as conn:
                row = self.find(conn, layout_id)
                if row is None:
                    return jsonify(error="not found"), 404
                conn.execute("UPDATE layout SET active=0")
                conn.execute(
                    "UPDATE layout SET active=1 WHERE id=?",
                    (layout_id,),
                )
                conn.execute("UPDATE layer SET active=0")
                return jsonify(self.row_to_dict(self.find(conn, layout_id)))

        @app.get("/api/layout/<int:layout_id>")
        def get_layout(layout_id: int):
            with self.db.connect() as conn:
                row = self.find(conn, layout_id)
                if row is None:
                    return jsonify(error="not found"), 404
                return jsonify(self.row_to_dict(row))

        @app.post("/api/layout")
        def create_layout():
            return self._write()

        @app.put("/api/layout/<int:layout_id>")
        def update_layout(layout_id: int):
            return self._write(layout_id)

        @app.delete("/api/layout/<int:layout_id>")
        def delete_layout(layout_id: int):
            with self.db.connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM layout WHERE id=?",
                    (layout_id,),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                return jsonify(ok=True)
