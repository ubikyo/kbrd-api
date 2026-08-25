import json
from dataclasses import asdict

from flask import Flask, jsonify, request

from kbrd_api.db import DB
from .geometry_layout import layout_geometry
from .geometry_svg import render_geometry_svg


GEOMETRY_COLUMNS = """
    id, name, description, author, unit, geometry, svg, active, created_at
"""


class Geometry:
    def __init__(self, db: DB):
        self.db = db

    @staticmethod
    def _row_to_dict(row) -> dict:
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
        }
        return result

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
            "geometry": json.dumps(
                geometry,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "svg": render_geometry_svg(layout, unit),
        }

    @staticmethod
    def _find(conn, geometry_id: int):
        return conn.execute(
            f"SELECT {GEOMETRY_COLUMNS} FROM geometry WHERE id=?",
            (geometry_id,),
        ).fetchone()

    def _write(self, geometry_id: int | None = None):
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
        )
        with self.db.connect() as conn:
            if geometry_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO geometry (name, description, author, unit, geometry, svg)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    fields,
                )
                geometry_id = cursor.lastrowid
                status = 201
            else:
                cursor = conn.execute(
                    """
                    UPDATE geometry
                    SET name=?, description=?, author=?, unit=?, geometry=?, svg=?
                    WHERE id=?
                    """,
                    (*fields, geometry_id),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                status = 200

            conn.commit()
            row = self._find(conn, geometry_id)
            return jsonify(self._row_to_dict(row)), status

    def register(self, app: Flask) -> None:
        @app.get("/api/geometry")
        def list_geometries():
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"SELECT {GEOMETRY_COLUMNS} FROM geometry ORDER BY name, id"
                ).fetchall()
                return jsonify([self._row_to_dict(row) for row in rows])

        @app.get("/api/geometry/active")
        def get_active_geometry():
            with self.db.connect() as conn:
                row = conn.execute(f"""
                    SELECT {GEOMETRY_COLUMNS}
                    FROM geometry
                    ORDER BY
                        active DESC,
                        CASE WHEN lower(name) = 'default' THEN 0 ELSE 1 END,
                        name,
                        id
                    LIMIT 1
                """).fetchone()
                if row is None:
                    return jsonify(error="not found"), 404
                return jsonify(self._row_to_dict(row))

        @app.put("/api/geometry/<int:geometry_id>/activate")
        def activate_geometry(geometry_id: int):
            with self.db.connect() as conn:
                row = self._find(conn, geometry_id)
                if row is None:
                    return jsonify(error="not found"), 404
                conn.execute("UPDATE geometry SET active=0")
                conn.execute(
                    "UPDATE geometry SET active=1 WHERE id=?",
                    (geometry_id,),
                )
                conn.execute("UPDATE workspace SET active=0")
                return jsonify(self._row_to_dict(self._find(conn, geometry_id)))

        @app.get("/api/geometry/<int:geometry_id>")
        def get_geometry(geometry_id: int):
            with self.db.connect() as conn:
                row = self._find(conn, geometry_id)
                if row is None:
                    return jsonify(error="not found"), 404
                return jsonify(self._row_to_dict(row))

        @app.post("/api/geometry")
        def create_geometry():
            return self._write()

        @app.put("/api/geometry/<int:geometry_id>")
        def update_geometry(geometry_id: int):
            return self._write(geometry_id)

        @app.delete("/api/geometry/<int:geometry_id>")
        def delete_geometry(geometry_id: int):
            with self.db.connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM geometry WHERE id=?",
                    (geometry_id,),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                return jsonify(ok=True)
