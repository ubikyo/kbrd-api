import json

from flask import Flask, request, jsonify

from kbrd_api.db import DB


class Geometry:
    def __init__(self, db: DB):
        self.db = db

    def _row_to_dict(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "author": row["author"],
            "unit": row["unit"],
            "geometry": json.loads(row["geometry"]),
            "created_at": row["created_at"],
        }

    def register(self, app: Flask) -> None:
        @app.get("/api/geometry")
        def list_geometries():
            with self.db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        created_at
                    FROM geometry
                    ORDER BY name, id
                    """
                ).fetchall()

                return jsonify([
                    self._row_to_dict(row)
                    for row in rows
                ])

        @app.get("/api/geometry/<int:geometry_id>")
        def get_geometry(geometry_id: int):
            with self.db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        created_at
                    FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                ).fetchone()

                if row is None:
                    return jsonify(error="not found"), 404

                return jsonify(self._row_to_dict(row))

        @app.post("/api/geometry")
        def create_geometry():
            data = request.get_json(silent=True) or {}

            name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            author = (data.get("author") or "").strip()
            unit = (data.get("unit") or "").strip()
            geometry = data.get("geometry")

            if not name:
                return jsonify(error="missing name"), 400

            if unit not in ("px", "mm"):
                return jsonify(error="unit must be 'px' or 'mm'"), 400

            if not isinstance(geometry, list):
                return jsonify(error="geometry must be an array"), 400

            try:
                geometry_json = json.dumps(
                    geometry,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                return jsonify(error="invalid geometry"), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO geometry(
                        name,
                        description,
                        author,
                        unit,
                        geometry
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        description,
                        author,
                        unit,
                        geometry_json,
                    ),
                )

                geometry_id = cur.lastrowid
                conn.commit()

                row = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        created_at
                    FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                ).fetchone()

                return jsonify(self._row_to_dict(row)), 201

        @app.put("/api/geometry/<int:geometry_id>")
        def update_geometry(geometry_id: int):
            data = request.get_json(silent=True) or {}

            name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            author = (data.get("author") or "").strip()
            unit = (data.get("unit") or "").strip()
            geometry = data.get("geometry")

            if not name:
                return jsonify(error="missing name"), 400

            if unit not in ("px", "mm"):
                return jsonify(error="unit must be 'px' or 'mm'"), 400

            if not isinstance(geometry, list):
                return jsonify(error="geometry must be an array"), 400

            try:
                geometry_json = json.dumps(
                    geometry,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                return jsonify(error="invalid geometry"), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE geometry
                    SET
                        name=?,
                        description=?,
                        author=?,
                        unit=?,
                        geometry=?
                    WHERE id=?
                    """,
                    (
                        name,
                        description,
                        author,
                        unit,
                        geometry_json,
                        geometry_id,
                    ),
                )

                conn.commit()

                if cur.rowcount == 0:
                    return jsonify(error="not found"), 404

                row = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        created_at
                    FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                ).fetchone()

                return jsonify(self._row_to_dict(row))

        @app.delete("/api/geometry/<int:geometry_id>")
        def delete_geometry(geometry_id: int):
            with self.db.connect() as conn:
                cur = conn.execute(
                    "DELETE FROM geometry WHERE id=?",
                    (geometry_id,),
                )

                conn.commit()

                if cur.rowcount == 0:
                    return jsonify(error="not found"), 404

                return jsonify(ok=True)