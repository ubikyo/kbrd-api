from pathlib import Path

from flask import Flask, send_file

# The spec sits beside the package rather than inside `api/`: it describes
# every one of these modules, not this one. `Makefile`'s own rsync copies
# `src/kbrd_api/` wholesale, so it ships to the device with the code and
# `/api/docs` works there too.
SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"

# Pinned rather than floating: an unpinned CDN tag turns a working page
# into a broken one the day upstream changes something, without a commit
# here to explain it.
SWAGGER_UI_VERSION = "5.32.15"
SWAGGER_UI_BASE = (
    f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
)

# Swagger UI parses YAML itself, so the spec is served as a static file and
# nothing here ever has to read it — which is what keeps PyYAML out of the
# device's own dependencies. It's a test-only requirement (see
# `tests/test_openapi.py`).
PAGE = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>KBRD-API</title>
    <link rel="stylesheet" href="{SWAGGER_UI_BASE}/swagger-ui.css">
  </head>
  <body>
    <div id="swagger"></div>
    <script src="{SWAGGER_UI_BASE}/swagger-ui-bundle.js"></script>
    <script>
      window.SwaggerUIBundle({{
        url: "/api/openapi.yaml",
        dom_id: "#swagger",
        // Ordered as the spec lists them, not alphabetically: the tags go
        // Health, Layout, Layer, Keys, ... which is roughly how a caller
        // works through them.
        docExpansion: "list",
        deepLinking: true,
      }});
    </script>
  </body>
</html>
"""


class Docs:
    """Serves KBRD-API's own contract: the spec at `/api/openapi.yaml`,
    and Swagger UI over it at `/api/docs`.

    Registered like every other API class (see `main.py`) and adding no
    dependency to the service — the assets come from a CDN, so `/api/docs`
    needs the *browser* to have internet access, not the keyboard. The
    spec itself is served locally and is readable without any of that.
    """

    def register(self, app: Flask) -> None:
        @app.get("/api/openapi.yaml")
        def openapi_spec():
            return send_file(SPEC_PATH, mimetype="application/yaml")

        @app.get("/api/docs")
        def api_docs():
            return PAGE
