from flask import Flask, request, jsonify

class Health:
    def register(self, app: Flask) -> None:
        @app.get("/api/health")
        def health():
            return jsonify(status="ok")
