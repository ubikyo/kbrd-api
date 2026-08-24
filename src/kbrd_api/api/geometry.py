import json

from flask import Flask, request, jsonify

from kbrd_api.db import DB
from .geometry_svg import generate_geometry_svg

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
            "svg": row["svg"],
            "created_at": row["created_at"],
        }

    def _prepare_geometry(
        self,
        geometry,
        unit: str,
    ) -> tuple[str, str]:
        if not isinstance(geometry, list):
            raise ValueError("geometry must be an array")

        for group_index, group in enumerate(geometry):
            if not isinstance(group, dict):
                raise ValueError(
                    f"group {group_index + 1} must be an object"
                )

            rows = group.get("elements")
            if not isinstance(rows, list):
                raise ValueError(
                    f"rows missing in group {group_index + 1}"
                )

            group_gap = group.get("gap", 0)
            if not isinstance(group_gap, (int, float)) or group_gap < 0:
                raise ValueError(
                    f"invalid gap in group {group_index + 1}"
                )

            for row_index, row in enumerate(rows):
                if not isinstance(row, list):
                    raise ValueError(
                        f"row {group_index + 1}:{row_index + 1} "
                        "must be an array"
                    )

                for item_index, item in enumerate(row):
                    if not isinstance(item, dict):
                        raise ValueError(
                            f"element {group_index + 1}:"
                            f"{row_index + 1}:{item_index + 1} "
                            "must be an object"
                        )

                    element_type = item.get("type", "key")
                    if element_type not in ("key", "space"):
                        raise ValueError(
                            f"invalid type at "
                            f"{group_index + 1}:{row_index + 1}:"
                            f"{item_index + 1}"
                        )

                    size = item.get("size")
                    quantity = item.get("quantity", 1)
                    rowspan = item.get("rowspan", 1)
                    colspan = item.get("colspan", 1)

                    if not isinstance(size, (int, float)) or size <= 0:
                        raise ValueError("invalid size")

                    if not isinstance(quantity, int) or quantity < 1:
                        raise ValueError("invalid quantity")

                    if not isinstance(rowspan, int) or rowspan < 0:
                        raise ValueError("invalid rowspan")

                    if not isinstance(colspan, int) or colspan < 0:
                        raise ValueError("invalid colspan")

                    parts = item.get("parts", [])
                    if not isinstance(parts, list):
                        raise ValueError("parts must be an array")

                    for part in parts:
                        if not isinstance(part, dict):
                            raise ValueError("part must be an object")
                    if parts:
                        for part in parts:
                            if (
                                not isinstance(part.get("width"), (int, float))
                                or not isinstance(part.get("height"), (int, float))
                                or part["width"] <= 0
                                or part["height"] <= 0
                                or part.get("align", "right")
                                not in ("left", "center", "right")
                            ):
                                raise ValueError("invalid composite part")

        geometry_json = json.dumps(
            geometry,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        svg = generate_geometry_svg(
            geometry=geometry,
            unit=unit,
        )

        return geometry_json, svg

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
                        svg,
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
                        svg,
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
                return jsonify(
                    error="unit must be 'px' or 'mm'"
                ), 400

            try:
                geometry_json, svg = self._prepare_geometry(
                    geometry,
                    unit,
                )
            except (TypeError, ValueError) as exc:
                return jsonify(
                    error=f"invalid geometry: {exc}"
                ), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO geometry (
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        svg
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        description,
                        author,
                        unit,
                        geometry_json,
                        svg,
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
                        svg,
                        created_at
                    FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                ).fetchone()

                return jsonify(
                    self._row_to_dict(row)
                ), 201

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
                return jsonify(
                    error="unit must be 'px' or 'mm'"
                ), 400

            try:
                geometry_json, svg = self._prepare_geometry(
                    geometry,
                    unit,
                )
            except (TypeError, ValueError) as exc:
                return jsonify(
                    error=f"invalid geometry: {exc}"
                ), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    """
                    UPDATE geometry
                    SET
                        name=?,
                        description=?,
                        author=?,
                        unit=?,
                        geometry=?,
                        svg=?
                    WHERE id=?
                    """,
                    (
                        name,
                        description,
                        author,
                        unit,
                        geometry_json,
                        svg,
                        geometry_id,
                    ),
                )

                conn.commit()

                if cur.rowcount == 0:
                    return jsonify(
                        error="not found"
                    ), 404

                row = conn.execute(
                    """
                    SELECT
                        id,
                        name,
                        description,
                        author,
                        unit,
                        geometry,
                        svg,
                        created_at
                    FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                ).fetchone()

                return jsonify(
                    self._row_to_dict(row)
                )

        @app.delete("/api/geometry/<int:geometry_id>")
        def delete_geometry(geometry_id: int):
            with self.db.connect() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM geometry
                    WHERE id=?
                    """,
                    (geometry_id,),
                )

                conn.commit()

                if cur.rowcount == 0:
                    return jsonify(
                        error="not found"
                    ), 404

                return jsonify(ok=True)