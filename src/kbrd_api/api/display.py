from flask import Flask, jsonify, request

from kbrd_api.db import DB


class Display:
    """The physical screen's own width/height — a single row shared by
    every layout (see `db.py`'s own comment on the `display` table for why
    this doesn't live on `layout` alongside Caps size / Gap size).

    That row also carries which screen this is (`name`/`brand`/`model`)
    and whether the setup wizard has ever run — both written by
    `api/setup.py` and neither touched here. A size changed by hand in
    Settings therefore leaves the name as the wizard set it: it says what
    panel was declared, not what the numbers have since become.
    """

    COLUMNS = "physical_width_mm, physical_height_mm, name, brand, model"

    def __init__(self, db: DB):
        self.db = db

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "physical_width_mm": row["physical_width_mm"],
            "physical_height_mm": row["physical_height_mm"],
            "name": row["name"],
            "brand": row["brand"],
            "model": row["model"],
        }

    @staticmethod
    def _positive_number(data, key: str, default: float) -> float:
        value = data.get(key, default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if number <= 0:
            raise ValueError(f"{key} must be greater than zero")
        return number

    def register(self, app: Flask) -> None:
        @app.get("/api/display")
        def get_display():
            with self.db.connect() as conn:
                row = conn.execute(
                    f"SELECT {self.COLUMNS} FROM display WHERE id=1"
                ).fetchone()
                return jsonify(self._row_to_dict(row))

        @app.put("/api/display")
        def update_display():
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error="body must be an object"), 400
            try:
                width = self._positive_number(
                    data, "physical_width_mm", 216
                )
                height = self._positive_number(
                    data, "physical_height_mm", 135
                )
            except ValueError as exc:
                return jsonify(error=str(exc)), 400

            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE display SET physical_width_mm=?, physical_height_mm=?
                    WHERE id=1
                    """,
                    (width, height),
                )
                conn.commit()
                row = conn.execute(
                    f"SELECT {self.COLUMNS} FROM display WHERE id=1"
                ).fetchone()
                return jsonify(self._row_to_dict(row))
