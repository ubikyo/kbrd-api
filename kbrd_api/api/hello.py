from flask import Flask, request, jsonify

class Hello:
    def register(self, app: Flask) -> None:
        @app.post("/api/hello")
        def hello():
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400
            return jsonify(message=f"hello {name}")

        @app.get("/api/health")
        def health():
            return jsonify(status="ok")
