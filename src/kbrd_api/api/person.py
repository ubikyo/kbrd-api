from flask import Flask, request, jsonify
from kbrd_api.db import DB

class Person:
    def __init__(self, db: DB):
        self.db = db

    def register(self, app: Flask) -> None:
        @app.get("/api/person")
        def list_persons():
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT id, first_name, last_name "
                    "FROM person ORDER BY last_name, first_name"
                ).fetchall()
                return jsonify([dict(r) for r in rows])

        @app.post("/api/person")
        def create_person():
            data = request.get_json(silent=True) or {}
            first_name = (data.get("first_name") or "").strip()
            last_name = (data.get("last_name") or "").strip()
            if not first_name or not last_name:
                return jsonify(error="missing first_name or last_name"), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO person(first_name, last_name) VALUES (?, ?)",
                    (first_name, last_name),
                )
                conn.commit()
                return jsonify(
                    id=cur.lastrowid,
                    first_name=first_name,
                    last_name=last_name,
                ), 201

        @app.put("/api/person/<int:person_id>")
        def update_person(person_id: int):
            data = request.get_json(silent=True) or {}
            first_name = (data.get("first_name") or "").strip()
            last_name = (data.get("last_name") or "").strip()
            if not first_name or not last_name:
                return jsonify(error="missing first_name or last_name"), 400

            with self.db.connect() as conn:
                cur = conn.execute(
                    "UPDATE person SET first_name=?, last_name=? WHERE id=?",
                    (first_name, last_name, person_id),
                )
                conn.commit()
                if cur.rowcount == 0:
                    return jsonify(error="not found"), 404

                return jsonify(
                    id=person_id,
                    first_name=first_name,
                    last_name=last_name,
                )

        @app.delete("/api/person/<int:person_id>")
        def delete_person(person_id: int):
            with self.db.connect() as conn:
                cur = conn.execute(
                    "DELETE FROM person WHERE id=?", (person_id,)
                )
                conn.commit()
                if cur.rowcount == 0:
                    return jsonify(error="not found"), 404
                return jsonify(ok=True)

        @app.get("/api/person/last")
        def get_last_person():
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT id, first_name, last_name "
                    "FROM person "
                    "ORDER BY id DESC "
                    "LIMIT 1"
                ).fetchone()

                # Pas de contenu => objet vide (200), côté client c'est simple
                if row is None:
                    return jsonify({}), 200

                return jsonify(dict(row)), 200
