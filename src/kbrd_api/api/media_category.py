import sqlite3

from flask import Flask, jsonify, request

from kbrd_api.db import DB

CATEGORY_COLUMNS = "id, name, created_at"


class MediaCategory:
    """The Media panel's own categories — the label an imported image or
    video is filed under (see kbrd-web's `menu/Category`, which is the
    only thing that reads or writes these).

    Kept apart from the media *files* themselves, which `Layer` serves
    under `/api/media` (it owns them: a file's lifetime is tied to the
    plugin configs that reference it). A category is a row of its own,
    outliving whatever is filed under it.
    """

    def __init__(self, db: DB, layer):
        self.db = db
        # `Layer` owns the media files themselves — where they're stored,
        # and what still points at one. Deleting a category has to reach
        # that to clear out what it takes with it.
        self.layer = layer

    @staticmethod
    def row_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _name(data) -> str:
        if not isinstance(data, dict):
            raise ValueError("body must be an object")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("missing name")
        return name

    @staticmethod
    def find(conn, category_id: int):
        return conn.execute(
            f"SELECT {CATEGORY_COLUMNS} FROM media_category WHERE id=?",
            (category_id,),
        ).fetchone()

    def register(self, app: Flask) -> None:
        @app.get("/api/media-category")
        def list_media_categories():
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"SELECT {CATEGORY_COLUMNS} FROM media_category "
                    "ORDER BY name, id"
                ).fetchall()
                return jsonify([self.row_to_dict(row) for row in rows])

        @app.post("/api/media-category")
        def create_media_category():
            try:
                name = self._name(request.get_json(silent=True))
            except (TypeError, ValueError) as exc:
                return jsonify(error=str(exc)), 400

            with self.db.connect() as conn:
                try:
                    cursor = conn.execute(
                        "INSERT INTO media_category (name) VALUES (?)",
                        (name,),
                    )
                # The `UNIQUE` on `name` (see `db.py`) is what actually
                # decides this, rather than a SELECT first: two calls
                # racing on the same name would both pass that check.
                except sqlite3.IntegrityError:
                    return jsonify(error="name already exists"), 409
                conn.commit()
                row = self.find(conn, cursor.lastrowid)
                return jsonify(self.row_to_dict(row)), 201

        @app.put("/api/media-category/<int:category_id>")
        def update_media_category(category_id: int):
            try:
                name = self._name(request.get_json(silent=True))
            except (TypeError, ValueError) as exc:
                return jsonify(error=str(exc)), 400

            with self.db.connect() as conn:
                try:
                    cursor = conn.execute(
                        "UPDATE media_category SET name=? WHERE id=?",
                        (name, category_id),
                    )
                except sqlite3.IntegrityError:
                    return jsonify(error="name already exists"), 409
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                conn.commit()
                return jsonify(self.row_to_dict(self.find(conn, category_id)))

        @app.delete("/api/media-category/<int:category_id>")
        def delete_media_category(category_id: int):
            with self.db.connect() as conn:
                if self.find(conn, category_id) is None:
                    return jsonify(error="not found"), 404
                # The library always keeps at least one category — there'd
                # be nothing left to file a media under otherwise (see
                # `db.py`, which seeds "Default" for the same reason).
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM media_category"
                ).fetchone()[0]
                if remaining <= 1:
                    return jsonify(error="cannot delete the last category"), 400
                # A category takes its medias with it (`ON DELETE
                # CASCADE`, see `db.py`), so their filenames are read
                # before the rows go — nothing else knows them afterwards.
                held = [
                    row["filename"]
                    for row in conn.execute(
                        "SELECT filename FROM media WHERE category_id=?",
                        (category_id,),
                    ).fetchall()
                ]
                cursor = conn.execute(
                    "DELETE FROM media_category WHERE id=?",
                    (category_id,),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                # The rows are gone; each file goes too, unless a plugin
                # config still draws with it — `discard_media` is what
                # decides, the same way it does for a plugin's own media.
                for filename in held:
                    self.layer.discard_media(conn, filename)
                conn.commit()
                return jsonify(ok=True)
