import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory

from kbrd_api.db import DB


class Workspace:
    ALLOWED_IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png"}
    ALLOWED_FONT_EXTENSIONS = {".otf", ".ttf"}

    def __init__(
        self,
        db: DB,
        geometry_api,
        media_dir: str,
        font_dir: str,
        bundled_font_dir: str,
    ):
        self.db = db
        self.geometry_api = geometry_api
        self.media_dir = Path(media_dir)
        self.font_dir = Path(font_dir)
        self.bundled_font_dir = Path(bundled_font_dir)

    @staticmethod
    def _media_names(config) -> set[str]:
        if not isinstance(config, dict):
            return set()
        candidates = [config.get("media")]
        down = config.get("down")
        if isinstance(down, dict) and isinstance(down.get("config"), dict):
            candidates.append(down["config"].get("media"))
        return {
            filename
            for filename in candidates
            if isinstance(filename, str) and Path(filename).name == filename
        }

    def _delete_media(self, config, keep=frozenset()) -> None:
        for filename in self._media_names(config) - set(keep):
            try:
                (self.media_dir / filename).unlink(missing_ok=True)
            except OSError:
                pass

    def _delete_plugin_media(self, row) -> None:
        if row is None or row["plugin_id"] != "kbrd.image":
            return
        try:
            self._delete_media(json.loads(row["config"]))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _plugin(row) -> dict:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "key_ref": row["key_ref"],
            "plugin_id": row["plugin_id"],
            "plugin_version": row["plugin_version"],
            "position": row["position"],
            "enabled": bool(row["enabled"]),
            "config": json.loads(row["config"]),
        }

    def _item(self, conn, row, include_plugins=False) -> dict:
        result = {
            "id": row["id"],
            "geometry_id": row["geometry_id"],
            "name": row["name"],
            "description": row["description"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }
        if include_plugins:
            plugins = conn.execute(
                """
                SELECT * FROM key_plugin
                WHERE workspace_id=?
                ORDER BY key_ref, position, id
                """,
                (row["id"],),
            ).fetchall()
            result["plugins"] = [self._plugin(plugin) for plugin in plugins]
            properties = conn.execute(
                """
                SELECT key_ref, config FROM key_property
                WHERE workspace_id=? ORDER BY key_ref
                """,
                (row["id"],),
            ).fetchall()
            result["key_properties"] = [
                {
                    "key_ref": item["key_ref"],
                    "config": json.loads(item["config"]),
                }
                for item in properties
            ]
        return result

    @staticmethod
    def _workspace(conn, workspace_id):
        return conn.execute(
            "SELECT * FROM workspace WHERE id=?",
            (workspace_id,),
        ).fetchone()

    def register(self, app: Flask) -> None:
        @app.get("/api/workspace")
        def list_all_workspaces():
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM workspace ORDER BY name, id"
                ).fetchall()
                return jsonify([self._item(conn, row) for row in rows])

        @app.get("/api/fonts")
        def list_fonts():
            filenames = set()
            for directory in (self.bundled_font_dir, self.font_dir):
                if directory.is_dir():
                    filenames.update(
                        path.name
                        for path in directory.iterdir()
                        if path.is_file()
                        and path.suffix.lower() in self.ALLOWED_FONT_EXTENSIONS
                    )
            return jsonify([
                {"value": filename, "label": Path(filename).stem}
                for filename in sorted(filenames, key=str.casefold)
            ])

        @app.get("/api/fonts/<filename>")
        def get_font(filename):
            if (
                Path(filename).name != filename
                or Path(filename).suffix.lower()
                not in self.ALLOWED_FONT_EXTENSIONS
            ):
                return jsonify(error="invalid font"), 400
            for directory in (self.font_dir, self.bundled_font_dir):
                if (directory / filename).is_file():
                    return send_from_directory(directory, filename)
            return jsonify(error="font not found"), 404

        @app.post("/api/medias")
        def upload_media():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                return jsonify(error="missing file"), 400
            extension = Path(uploaded.filename).suffix.lower()
            if (
                extension not in self.ALLOWED_IMAGE_EXTENSIONS
                or not (uploaded.mimetype or "").startswith("image/")
            ):
                return jsonify(error="invalid image"), 400
            try:
                self.media_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{uuid4().hex}{extension}"
                uploaded.save(self.media_dir / filename)
            except OSError as exc:
                app.logger.exception("Unable to store uploaded media")
                return jsonify(error=f"media storage unavailable: {exc.strerror}"), 500
            return jsonify(filename=filename), 201

        @app.get("/api/medias/<filename>")
        def get_media(filename):
            return send_from_directory(self.media_dir, filename)

        @app.get("/api/geometry/<int:geometry_id>/workspace")
        def list_workspaces(geometry_id):
            with self.db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM workspace
                    WHERE geometry_id=?
                    ORDER BY name, id
                    """,
                    (geometry_id,),
                ).fetchall()
                return jsonify([
                    self._item(conn, row, include_plugins=True)
                    for row in rows
                ])

        @app.post("/api/geometry/<int:geometry_id>/workspace")
        def create_workspace(geometry_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            try:
                with self.db.connect() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO workspace(geometry_id, name, description)
                        VALUES (?, ?, ?)
                        """,
                        (
                            geometry_id,
                            name,
                            str(data.get("description") or "").strip(),
                        ),
                    )
                    row = self._workspace(conn, cursor.lastrowid)
                    return jsonify(self._item(conn, row, True)), 201
            except sqlite3.IntegrityError:
                return jsonify(error="geometry not found"), 404

        @app.put("/api/workspace/<int:workspace_id>/activate")
        def activate_workspace(workspace_id):
            with self.db.connect() as conn:
                row = self._workspace(conn, workspace_id)
                if row is None:
                    return jsonify(error="not found"), 404
                conn.execute("UPDATE workspace SET active=0")
                conn.execute(
                    "UPDATE workspace SET active=1 WHERE id=?",
                    (workspace_id,),
                )
                conn.execute("UPDATE geometry SET active=0")
                conn.execute(
                    "UPDATE geometry SET active=1 WHERE id=?",
                    (row["geometry_id"],),
                )
                row = self._workspace(conn, workspace_id)
                return jsonify(self._item(conn, row, True))

        @app.delete("/api/workspace/active")
        def deactivate_workspace():
            with self.db.connect() as conn:
                conn.execute("UPDATE workspace SET active=0")
                return jsonify(ok=True)

        @app.put("/api/workspace/<int:workspace_id>")
        def update_workspace(workspace_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            with self.db.connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE workspace SET name=?, description=? WHERE id=?
                    """,
                    (
                        name,
                        str(data.get("description") or "").strip(),
                        workspace_id,
                    ),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                row = self._workspace(conn, workspace_id)
                return jsonify(self._item(conn, row, True))

        @app.delete("/api/workspace/<int:workspace_id>")
        def delete_workspace(workspace_id):
            with self.db.connect() as conn:
                plugin_rows = conn.execute(
                    "SELECT * FROM key_plugin WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchall()
                cursor = conn.execute(
                    "DELETE FROM workspace WHERE id=?",
                    (workspace_id,),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                for plugin_row in plugin_rows:
                    self._delete_plugin_media(plugin_row)
                return jsonify(ok=True)

        @app.post("/api/workspace/<int:workspace_id>/keys/<key_ref>/plugins")
        def add_plugin(workspace_id, key_ref):
            data = request.get_json(silent=True) or {}
            plugin_id = str(data.get("plugin_id") or "").strip()
            if not plugin_id:
                return jsonify(error="missing plugin_id"), 400

            try:
                config = json.dumps(data.get("config") or {})
            except (TypeError, ValueError):
                return jsonify(error="invalid config"), 400

            with self.db.connect() as conn:
                if self._workspace(conn, workspace_id) is None:
                    return jsonify(error="workspace not found"), 404
                position = conn.execute(
                    """
                    SELECT COALESCE(MAX(position) + 1, 0)
                    FROM key_plugin WHERE workspace_id=? AND key_ref=?
                    """,
                    (workspace_id, key_ref),
                ).fetchone()[0]
                cursor = conn.execute(
                    """
                    INSERT INTO key_plugin(
                        workspace_id, key_ref, plugin_id,
                        plugin_version, position, config
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        key_ref,
                        plugin_id,
                        str(data.get("plugin_version") or "1.0.0"),
                        position,
                        config,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM key_plugin WHERE id=?",
                    (cursor.lastrowid,),
                ).fetchone()
                return jsonify(self._plugin(row)), 201

        @app.put("/api/key-plugin/<int:plugin_id>")
        def update_plugin(plugin_id):
            data = request.get_json(silent=True) or {}
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM key_plugin WHERE id=?",
                    (plugin_id,),
                ).fetchone()
                if row is None:
                    return jsonify(error="not found"), 404
                previous_config = json.loads(row["config"])
                try:
                    config = json.dumps(
                        data.get("config", json.loads(row["config"]))
                    )
                except (TypeError, ValueError):
                    return jsonify(error="invalid config"), 400
                conn.execute(
                    """
                    UPDATE key_plugin SET position=?, enabled=?, config=?
                    WHERE id=?
                    """,
                    (
                        data.get("position", row["position"]),
                        int(data.get("enabled", bool(row["enabled"]))),
                        config,
                        plugin_id,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM key_plugin WHERE id=?",
                    (plugin_id,),
                ).fetchone()
                current_config = json.loads(row["config"])
                if row["plugin_id"] == "kbrd.image":
                    self._delete_media(
                        previous_config,
                        keep=self._media_names(current_config),
                    )
                return jsonify(self._plugin(row))

        @app.put("/api/workspace/<int:workspace_id>/keys/<key_ref>/properties")
        def update_key_properties(workspace_id, key_ref):
            data = request.get_json(silent=True) or {}
            try:
                config = json.dumps(data.get("config") or {})
            except (TypeError, ValueError):
                return jsonify(error="invalid config"), 400
            with self.db.connect() as conn:
                if self._workspace(conn, workspace_id) is None:
                    return jsonify(error="workspace not found"), 404
                conn.execute(
                    """
                    INSERT INTO key_property(workspace_id, key_ref, config)
                    VALUES (?, ?, ?)
                    ON CONFLICT(workspace_id, key_ref)
                    DO UPDATE SET config=excluded.config
                    """,
                    (workspace_id, key_ref, config),
                )
                return jsonify(key_ref=key_ref, config=json.loads(config))

        @app.delete("/api/key-plugin/<int:plugin_id>")
        def delete_plugin(plugin_id):
            with self.db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM key_plugin WHERE id=?",
                    (plugin_id,),
                ).fetchone()
                cursor = conn.execute(
                    "DELETE FROM key_plugin WHERE id=?",
                    (plugin_id,),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                self._delete_plugin_media(row)
                return jsonify(ok=True)

        @app.get("/api/workspace/active")
        def active_workspace():
            with self.db.connect() as conn:
                workspace = conn.execute(
                    "SELECT * FROM workspace WHERE active=1 LIMIT 1"
                ).fetchone()
                if workspace is not None:
                    geometry = self.geometry_api._find(
                        conn,
                        workspace["geometry_id"],
                    )
                    return jsonify(
                        workspace=self._item(conn, workspace, True),
                        geometry=self.geometry_api._row_to_dict(geometry),
                    )

                geometry = conn.execute("""
                    SELECT * FROM geometry
                    ORDER BY
                        active DESC,
                        CASE WHEN lower(name) = 'default' THEN 0 ELSE 1 END,
                        name,
                        id
                    LIMIT 1
                """).fetchone()
                if geometry is None:
                    return jsonify(error="not found"), 404
                return jsonify(
                    workspace=None,
                    geometry=self.geometry_api._row_to_dict(geometry),
                )
