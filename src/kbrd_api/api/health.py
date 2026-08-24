from flask import Flask, jsonify


class Health:
    def register(self, app: Flask) -> None:
        @app.get("/api/health")
        def health():
            return jsonify(status="ok")
