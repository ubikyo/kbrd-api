import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory

from kbrd_api.db import DB


MEDIA_COLUMNS = "id, kind, category_id, filename, name, created_at"


class Layer:
    # What the device can actually play, which is what decides these — a
    # file it can't render is worse stored than refused.
    #
    # Images go through Kivy's SDL2_image loader. It is built here with
    # more than this (BMP, GIF and a run of legacy formats), but PNG and
    # JPEG are the two kept: the rest buy nothing a key's artwork needs.
    #
    # Videos go through Kivy's gstplayer, and these are the containers it
    # has a *native* GStreamer demuxer for on the device: `isomp4` for
    # MP4/M4V/MOV, `matroska` for MKV/WebM, `avi` for AVI. MPEG-TS
    # (.m2ts/.mts), MPEG-PS (.mpg/.mpeg), ASF (.wmv), FLV and animated GIF
    # are deliberately left out: ffmpeg can demux all of them and
    # `gst1-libav` would register `avdemux_*` elements for them, but at a
    # lower rank than a native demuxer — a fallback, not a guarantee.
    # Adding any of them means enabling its own plugin in
    # `kbrd_defconfig` first (`…PLUGIN_MPEGTSDEMUX`, `…PLUGIN_FLV`,
    # `GST1_PLUGINS_UGLY` for ASF).
    ALLOWED_IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png"}
    ALLOWED_VIDEO_EXTENSIONS = {
        ".avi",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".webm",
    }
    # A video's declared type can't quite be held to `video/…` the way an
    # image's can: whether a browser can name `.mkv` or `.avi` at all
    # depends on the machine it runs on, and one that can't sends nothing.
    # Where there *is* a type it still has to be a video's; where there
    # isn't, the extension carries the check alone.
    AMBIGUOUS_VIDEO_MIMETYPES = {"", "application/octet-stream"}
    ALLOWED_FONT_EXTENSIONS = {".otf", ".ttf"}
    MEDIA_PLUGIN_IDS = ("kbrd.render-image", "kbrd.render-video")

    def __init__(
        self,
        db: DB,
        layout_api,
        media_dir: str,
        font_dir: str,
        bundled_font_dir: str,
    ):
        self.db = db
        self.layout_api = layout_api
        # Resolved, and that matters: an upload is written with a plain
        # filesystem path (relative to the working directory), but reads go
        # through Flask's `send_from_directory`, which resolves a relative
        # path against the *application root* — the package folder — not the
        # working directory. Configured relatively (as `dev.sh` does), the
        # two point at different places and every stored file comes back a
        # 404. Absolute paths, which is what a device uses, were never
        # affected; resolving here fixes it for any configuration.
        self.media_dir = Path(media_dir).resolve()
        self.font_dir = Path(font_dir).resolve()
        self.bundled_font_dir = Path(bundled_font_dir).resolve()

    @staticmethod
    def media_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "category_id": row["category_id"],
            # The generated name the file is stored under, and what
            # `GET /api/media/<filename>` serves it back from.
            "filename": row["filename"],
            # The name it was dropped under.
            "name": row["name"],
            "created_at": row["created_at"],
        }

    def _uploaded_kind(self, uploaded) -> str | None:
        """Which kind an upload would be filed under, or `None` if the
        device could not render it at all — the extension and the declared
        type together (mirrored in kbrd-web's own `mediaKindOf`, which
        makes the same call before a file is even sent)."""
        extension = Path(uploaded.filename).suffix.lower()
        mimetype = uploaded.mimetype or ""
        if extension in self.ALLOWED_IMAGE_EXTENSIONS and mimetype.startswith(
            "image/"
        ):
            return "photo"
        if extension in self.ALLOWED_VIDEO_EXTENSIONS and (
            mimetype.startswith("video/")
            or mimetype in self.AMBIGUOUS_VIDEO_MIMETYPES
        ):
            return "video"
        return None

    def _store(self, uploaded) -> str:
        """Writes an accepted upload under a generated name and hands that
        name back — what a library row and a plugin config both refer to.
        Raises `OSError` if it could not be written."""
        self.media_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}{Path(uploaded.filename).suffix.lower()}"
        uploaded.save(self.media_dir / filename)
        return filename

    @staticmethod
    def _is_stored_name(filename: str) -> bool:
        """Whether a filename off the URL names a file in the media
        directory and nothing above it."""
        return Path(filename).name == filename

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

    @staticmethod
    def _media_is_in_library(conn, filename: str) -> bool:
        """Whether the Media panel's own library still holds this file.

        A library row is a reference in its own right: without this, a file
        dropped into the panel, attached to a key and then removed from
        that key would be collected below while the library still listed
        it."""
        return (
            conn.execute(
                "SELECT 1 FROM media WHERE filename=? LIMIT 1",
                (filename,),
            ).fetchone()
            is not None
        )

    def _media_is_referenced(self, conn, filename: str) -> bool:
        placeholders = ",".join("?" for _ in self.MEDIA_PLUGIN_IDS)
        rows = conn.execute(
            f"SELECT config FROM key_plugin WHERE plugin_id IN ({placeholders})",
            self.MEDIA_PLUGIN_IDS,
        ).fetchall()
        for row in rows:
            try:
                if filename in self._media_names(json.loads(row["config"])):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def discard_media(self, conn, filename: str) -> None:
        """Drop a stored file once nothing points at it any more.

        Two things can point at one: the Media panel's own library, and any
        plugin config using it. A file outlives both before it goes, which
        is what lets a media be deleted from the library while a key still
        draws with it — and the other way round."""
        if self._media_is_in_library(conn, filename):
            return
        if self._media_is_referenced(conn, filename):
            return
        try:
            (self.media_dir / filename).unlink(missing_ok=True)
        except OSError:
            pass

    def _delete_media(self, conn, config, keep=frozenset()) -> None:
        for filename in self._media_names(config) - set(keep):
            self.discard_media(conn, filename)

    def _delete_plugin_media(self, conn, row) -> None:
        if row is None or row["plugin_id"] not in self.MEDIA_PLUGIN_IDS:
            return
        try:
            self._delete_media(conn, json.loads(row["config"]))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _plugin(row) -> dict:
        return {
            "id": row["id"],
            "layer_id": row["layer_id"],
            "key_ref": row["key_ref"],
            "plugin_id": row["plugin_id"],
            "plugin_version": row["plugin_version"],
            "position": row["position"],
            "enabled": bool(row["enabled"]),
            "config": json.loads(row["config"]),
        }

    def _item(self, conn, row, include_plugins=False) -> dict:
        factory_layout = row["factory_layout"]
        result = {
            "id": row["id"],
            "layout_id": row["layout_id"],
            "name": row["name"],
            "description": row["description"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            # KBRD-WEB's own `<Factory>` grid disposition (rows/cells/merge
            # groups) — opaque to KBRD-API, stored and returned as-is.
            "factory_layout": (
                json.loads(factory_layout) if factory_layout else None
            ),
        }
        if include_plugins:
            plugins = conn.execute(
                """
                SELECT * FROM key_plugin
                WHERE layer_id=?
                ORDER BY key_ref, position, id
                """,
                (row["id"],),
            ).fetchall()
            result["plugins"] = [self._plugin(plugin) for plugin in plugins]
            properties = conn.execute(
                """
                SELECT key_ref, config FROM key_property
                WHERE layer_id=? ORDER BY key_ref
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
    def _layer(conn, layer_id):
        return conn.execute(
            "SELECT * FROM layer WHERE id=?",
            (layer_id,),
        ).fetchone()

    @staticmethod
    def _clone_layer_row(conn, source_layer_id, dest_layout_id, name=None, description=None) -> int:
        """Inserts a new `layer` row under `dest_layout_id`, copying the
        source's own `factory_layout` (the Layout-mode grid disposition)
        verbatim. `name`/`description` override the source's own when
        given (a real "Duplicate <layer>" with a new name); left `None`
        they fall back to the source's — used when cascading a Layout
        duplicate/replace, where each layer keeps its own name."""
        cursor = conn.execute(
            """
            INSERT INTO layer(layout_id, name, description, factory_layout)
            SELECT ?, COALESCE(?, name), COALESCE(?, description), factory_layout
            FROM layer WHERE id=?
            """,
            (dest_layout_id, name, description, source_layer_id),
        )
        return cursor.lastrowid

    @staticmethod
    def _clone_layer_content(conn, source_layer_id, dest_layer_id) -> None:
        """Copies every `key_plugin`/`key_property` row from one layer to
        another, keyed the same (same `key_ref`s) — used by both a layer
        duplicate/replace and each layer cascaded from a Layout
        duplicate/replace."""
        conn.execute(
            """
            INSERT INTO key_plugin(
                layer_id, key_ref, plugin_id, plugin_version, position, enabled, config
            )
            SELECT ?, key_ref, plugin_id, plugin_version, position, enabled, config
            FROM key_plugin WHERE layer_id=?
            """,
            (dest_layer_id, source_layer_id),
        )
        conn.execute(
            """
            INSERT INTO key_property(layer_id, key_ref, config)
            SELECT ?, key_ref, config FROM key_property WHERE layer_id=?
            """,
            (dest_layer_id, source_layer_id),
        )

    def _clear_layer_content(self, conn, layer_id) -> None:
        """Like `clear_key`, but for every key on the layer at once — used
        before a layer/layout replace overwrites a target's content."""
        plugin_rows = conn.execute(
            "SELECT * FROM key_plugin WHERE layer_id=?",
            (layer_id,),
        ).fetchall()
        conn.execute("DELETE FROM key_plugin WHERE layer_id=?", (layer_id,))
        conn.execute("DELETE FROM key_property WHERE layer_id=?", (layer_id,))
        for plugin_row in plugin_rows:
            self._delete_plugin_media(conn, plugin_row)

    def register(self, app: Flask) -> None:
        @app.get("/api/layer")
        def list_all_layers():
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM layer ORDER BY name, id"
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

        @app.post("/api/media")
        def upload_media():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                return jsonify(error="missing file"), 400
            kind = self._uploaded_kind(uploaded)
            if kind is None:
                return jsonify(error="invalid media"), 400
            try:
                filename = self._store(uploaded)
            except OSError as exc:
                app.logger.exception("Unable to store uploaded media")
                return jsonify(error=f"media storage unavailable: {exc.strerror}"), 500

            # `category_id` is what turns an upload into a library entry
            # (see KBRD-WEB's Media panel). Without it this is just a file
            # store, which is all the plugin editors have ever needed from
            # it — and they keep getting exactly the `{filename}` they
            # already read.
            category_id = request.form.get("category_id")
            if category_id is None:
                return jsonify(filename=filename), 201

            with self.db.connect() as conn:
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO media (kind, category_id, filename, name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            kind,
                            int(category_id),
                            filename,
                            uploaded.filename,
                        ),
                    )
                except (ValueError, sqlite3.IntegrityError):
                    # No such category, most likely. The file is already
                    # written, so it goes back out rather than being left
                    # behind with nothing pointing at it.
                    (self.media_dir / filename).unlink(missing_ok=True)
                    return jsonify(error="unknown category"), 400
                conn.commit()
                row = conn.execute(
                    f"SELECT {MEDIA_COLUMNS} FROM media WHERE id=?",
                    (cursor.lastrowid,),
                ).fetchone()
                return jsonify(self.media_to_dict(row)), 201

        @app.get("/api/media")
        def list_medias():
            with self.db.connect() as conn:
                rows = conn.execute(
                    f"SELECT {MEDIA_COLUMNS} FROM media ORDER BY id"
                ).fetchall()
                return jsonify([self.media_to_dict(row) for row in rows])

        @app.get("/api/media/<filename>")
        def get_media(filename):
            return send_from_directory(self.media_dir, filename)

        @app.put("/api/media/<filename>")
        def replace_media(filename):
            # The same library entry with a new file behind it — what the
            # Media panel does when one media is dropped onto another's
            # square. The entry keeps its id and its category; what it is
            # stored as, and the name it was dropped under, are the new
            # file's.
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                return jsonify(error="missing file"), 400
            kind = self._uploaded_kind(uploaded)
            if kind is None:
                return jsonify(error="invalid media"), 400
            if not self._is_stored_name(filename):
                return jsonify(error="not found"), 404

            with self.db.connect() as conn:
                row = conn.execute(
                    f"SELECT {MEDIA_COLUMNS} FROM media WHERE filename=?",
                    (filename,),
                ).fetchone()
                if row is None:
                    return jsonify(error="not found"), 404
                # An entry stays in the tab it is being shown under: a
                # photo is replaced by a photo, a video by a video.
                if kind != row["kind"]:
                    return jsonify(error=f"expected a {row['kind']}"), 400
                try:
                    stored = self._store(uploaded)
                except OSError as exc:
                    app.logger.exception("Unable to store uploaded media")
                    return jsonify(error=f"media storage unavailable: {exc.strerror}"), 500
                conn.execute(
                    "UPDATE media SET filename=?, name=? WHERE id=?",
                    (stored, uploaded.filename, row["id"]),
                )
                conn.commit()
                # Nothing in the library holds the old file any more, so
                # it goes — unless a plugin config still draws with it: a
                # key that was given that media keeps what it was given.
                self.discard_media(conn, filename)
                updated = conn.execute(
                    f"SELECT {MEDIA_COLUMNS} FROM media WHERE id=?",
                    (row["id"],),
                ).fetchone()
                return jsonify(self.media_to_dict(updated))

        @app.delete("/api/media/<filename>")
        def delete_media(filename):
            # Takes the file with it unless a plugin config still points
            # at it, exactly as deleting the whole category does.
            if not self._is_stored_name(filename):
                return jsonify(error="not found"), 404
            with self.db.connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM media WHERE filename=?", (filename,)
                )
                conn.commit()
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                self.discard_media(conn, filename)
                return jsonify(ok=True)

        @app.get("/api/layout/<int:layout_id>/layer")
        def list_layers(layout_id):
            with self.db.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM layer
                    WHERE layout_id=?
                    ORDER BY name, id
                    """,
                    (layout_id,),
                ).fetchall()
                return jsonify([
                    self._item(conn, row, include_plugins=True)
                    for row in rows
                ])

        @app.post("/api/layout/<int:layout_id>/layer")
        def create_layer(layout_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            try:
                with self.db.connect() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO layer(layout_id, name, description)
                        VALUES (?, ?, ?)
                        """,
                        (
                            layout_id,
                            name,
                            str(data.get("description") or "").strip(),
                        ),
                    )
                    row = self._layer(conn, cursor.lastrowid)
                    return jsonify(self._item(conn, row, True)), 201
            except sqlite3.IntegrityError:
                return jsonify(error="layout not found"), 404

        @app.put("/api/layer/<int:layer_id>/activate")
        def activate_layer(layer_id):
            with self.db.connect() as conn:
                row = self._layer(conn, layer_id)
                if row is None:
                    return jsonify(error="not found"), 404
                conn.execute("UPDATE layer SET active=0")
                conn.execute(
                    "UPDATE layer SET active=1 WHERE id=?",
                    (layer_id,),
                )
                conn.execute("UPDATE layout SET active=0")
                conn.execute(
                    "UPDATE layout SET active=1 WHERE id=?",
                    (row["layout_id"],),
                )
                row = self._layer(conn, layer_id)
                return jsonify(self._item(conn, row, True))

        @app.delete("/api/layer/active")
        def deactivate_layer():
            with self.db.connect() as conn:
                conn.execute("UPDATE layer SET active=0")
                return jsonify(ok=True)

        @app.put("/api/layer/<int:layer_id>")
        def update_layer(layer_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            with self.db.connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE layer SET name=?, description=? WHERE id=?
                    """,
                    (
                        name,
                        str(data.get("description") or "").strip(),
                        layer_id,
                    ),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                row = self._layer(conn, layer_id)
                return jsonify(self._item(conn, row, True))

        @app.put("/api/layer/<int:layer_id>/factory-layout")
        def update_factory_layout(layer_id):
            data = request.get_json(silent=True) or {}
            factory_layout = data.get("factory_layout")
            if factory_layout is not None and not isinstance(factory_layout, dict):
                return jsonify(error="factory_layout must be an object or null"), 400

            with self.db.connect() as conn:
                if self._layer(conn, layer_id) is None:
                    return jsonify(error="layer not found"), 404
                conn.execute(
                    "UPDATE layer SET factory_layout=? WHERE id=?",
                    (
                        json.dumps(
                            factory_layout,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if factory_layout is not None
                        else None,
                        layer_id,
                    ),
                )
                row = self._layer(conn, layer_id)
                return jsonify(self._item(conn, row, True))

        @app.delete("/api/layer/<int:layer_id>")
        def delete_layer(layer_id):
            with self.db.connect() as conn:
                layer = self._layer(conn, layer_id)
                if layer is None:
                    return jsonify(error="not found"), 404
                # A layout must always keep at least one layer — there'd
                # be nothing left to configure in Mapping mode otherwise
                # (see `Layout._write`'s own "Default" layer on create).
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM layer WHERE layout_id=?",
                    (layer["layout_id"],),
                ).fetchone()[0]
                if remaining <= 1:
                    return jsonify(error="cannot delete the last layer"), 400
                plugin_rows = conn.execute(
                    "SELECT * FROM key_plugin WHERE layer_id=?",
                    (layer_id,),
                ).fetchall()
                cursor = conn.execute(
                    "DELETE FROM layer WHERE id=?",
                    (layer_id,),
                )
                if cursor.rowcount == 0:
                    return jsonify(error="not found"), 404
                for plugin_row in plugin_rows:
                    self._delete_plugin_media(conn, plugin_row)
                return jsonify(ok=True)

        @app.post("/api/layer/<int:layer_id>/duplicate")
        def duplicate_layer(layer_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            with self.db.connect() as conn:
                source = self._layer(conn, layer_id)
                if source is None:
                    return jsonify(error="not found"), 404
                new_id = self._clone_layer_row(
                    conn,
                    layer_id,
                    source["layout_id"],
                    name,
                    str(data.get("description") or "").strip(),
                )
                self._clone_layer_content(conn, layer_id, new_id)
                row = self._layer(conn, new_id)
                return jsonify(self._item(conn, row, True)), 201

        @app.post("/api/layer/<int:layer_id>/replace")
        def replace_layer(layer_id):
            data = request.get_json(silent=True) or {}
            try:
                source_id = int(data.get("source_id"))
            except (TypeError, ValueError):
                return jsonify(error="missing source_id"), 400
            if source_id == layer_id:
                return jsonify(error="source and target are identical"), 400

            with self.db.connect() as conn:
                target = self._layer(conn, layer_id)
                source = self._layer(conn, source_id)
                if target is None or source is None:
                    return jsonify(error="not found"), 404
                self._clear_layer_content(conn, layer_id)
                conn.execute(
                    """
                    UPDATE layer SET factory_layout=(
                        SELECT factory_layout FROM layer WHERE id=?
                    ) WHERE id=?
                    """,
                    (source_id, layer_id),
                )
                self._clone_layer_content(conn, source_id, layer_id)
                row = self._layer(conn, layer_id)
                return jsonify(self._item(conn, row, True))

        @app.post("/api/layout/<int:layout_id>/duplicate")
        def duplicate_layout(layout_id):
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify(error="missing name"), 400

            with self.db.connect() as conn:
                source = self.layout_api.find(conn, layout_id)
                if source is None:
                    return jsonify(error="not found"), 404
                cursor = conn.execute(
                    """
                    INSERT INTO layout(
                        name, description, author, unit, geometry,
                        unit_mm, gap_mm, max_columns, max_rows
                    )
                    SELECT ?, ?, author, unit, '[]',
                           unit_mm, gap_mm, max_columns, max_rows
                    FROM layout WHERE id=?
                    """,
                    (name, str(data.get("description") or "").strip(), layout_id),
                )
                new_layout_id = cursor.lastrowid
                source_layers = conn.execute(
                    "SELECT id FROM layer WHERE layout_id=? ORDER BY name, id",
                    (layout_id,),
                ).fetchall()
                for layer_row in source_layers:
                    new_layer_id = self._clone_layer_row(
                        conn, layer_row["id"], new_layout_id
                    )
                    self._clone_layer_content(conn, layer_row["id"], new_layer_id)
                new_row = self.layout_api.find(conn, new_layout_id)
                return jsonify(self.layout_api.row_to_dict(new_row)), 201

        @app.post("/api/layout/<int:layout_id>/replace")
        def replace_layout(layout_id):
            data = request.get_json(silent=True) or {}
            try:
                source_id = int(data.get("source_id"))
            except (TypeError, ValueError):
                return jsonify(error="missing source_id"), 400
            if source_id == layout_id:
                return jsonify(error="source and target are identical"), 400

            with self.db.connect() as conn:
                target = self.layout_api.find(conn, layout_id)
                source = self.layout_api.find(conn, source_id)
                if target is None or source is None:
                    return jsonify(error="not found"), 404
                conn.execute(
                    """
                    UPDATE layout SET
                        author=?, unit=?,
                        unit_mm=?, gap_mm=?, max_columns=?, max_rows=?
                    WHERE id=?
                    """,
                    (
                        source["author"],
                        source["unit"],
                        source["unit_mm"],
                        source["gap_mm"],
                        source["max_columns"],
                        source["max_rows"],
                        layout_id,
                    ),
                )
                old_layer_ids = [
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM layer WHERE layout_id=?",
                        (layout_id,),
                    ).fetchall()
                ]
                if old_layer_ids:
                    placeholders = ",".join("?" for _ in old_layer_ids)
                    plugin_rows = conn.execute(
                        f"SELECT * FROM key_plugin WHERE layer_id IN ({placeholders})",
                        old_layer_ids,
                    ).fetchall()
                    conn.execute(
                        "DELETE FROM layer WHERE layout_id=?",
                        (layout_id,),
                    )
                    for plugin_row in plugin_rows:
                        self._delete_plugin_media(conn, plugin_row)
                source_layers = conn.execute(
                    "SELECT id FROM layer WHERE layout_id=? ORDER BY name, id",
                    (source_id,),
                ).fetchall()
                for layer_row in source_layers:
                    new_layer_id = self._clone_layer_row(
                        conn, layer_row["id"], layout_id
                    )
                    self._clone_layer_content(conn, layer_row["id"], new_layer_id)
                new_row = self.layout_api.find(conn, layout_id)
                return jsonify(self.layout_api.row_to_dict(new_row))

        @app.post("/api/layer/<int:layer_id>/keys/<key_ref>/plugins")
        def add_plugin(layer_id, key_ref):
            data = request.get_json(silent=True) or {}
            plugin_id = str(data.get("plugin_id") or "").strip()
            if not plugin_id:
                return jsonify(error="missing plugin_id"), 400

            try:
                config = json.dumps(data.get("config") or {})
            except (TypeError, ValueError):
                return jsonify(error="invalid config"), 400

            with self.db.connect() as conn:
                if self._layer(conn, layer_id) is None:
                    return jsonify(error="layer not found"), 404
                position = conn.execute(
                    """
                    SELECT COALESCE(MAX(position) + 1, 0)
                    FROM key_plugin WHERE layer_id=? AND key_ref=?
                    """,
                    (layer_id, key_ref),
                ).fetchone()[0]
                cursor = conn.execute(
                    """
                    INSERT INTO key_plugin(
                        layer_id, key_ref, plugin_id,
                        plugin_version, position, config
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        layer_id,
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

        @app.post(
            "/api/layer/<int:layer_id>/keys/<key_ref>/plugins/duplicate-from"
        )
        def duplicate_plugins(layer_id, key_ref):
            data = request.get_json(silent=True) or {}
            source_key_ref = str(data.get("source_key_ref") or "").strip()
            if not source_key_ref:
                return jsonify(error="missing source_key_ref"), 400

            with self.db.connect() as conn:
                if self._layer(conn, layer_id) is None:
                    return jsonify(error="layer not found"), 404
                source_plugins = conn.execute(
                    """
                    SELECT * FROM key_plugin
                    WHERE layer_id=? AND key_ref=?
                    ORDER BY position, id
                    """,
                    (layer_id, source_key_ref),
                ).fetchall()
                if source_plugins:
                    conn.execute(
                        """
                        UPDATE key_plugin SET position=position + ?
                        WHERE layer_id=? AND key_ref=?
                        """,
                        (len(source_plugins), layer_id, key_ref),
                    )
                    for position, plugin in enumerate(source_plugins):
                        conn.execute(
                            """
                            INSERT INTO key_plugin(
                                layer_id, key_ref, plugin_id,
                                plugin_version, position, enabled, config
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                layer_id,
                                key_ref,
                                plugin["plugin_id"],
                                plugin["plugin_version"],
                                position,
                                plugin["enabled"],
                                plugin["config"],
                            ),
                        )
                # Carries the source key's own properties along with its
                # plugins — a "Copy" of a key's Mapping content should
                # bring everything that makes up its look/behavior, not
                # just the plugin list (see kbrd-web's own `key_property`
                # usage, e.g. a key's own font/label config).
                source_property = conn.execute(
                    """
                    SELECT config FROM key_property
                    WHERE layer_id=? AND key_ref=?
                    """,
                    (layer_id, source_key_ref),
                ).fetchone()
                if source_property is not None:
                    conn.execute(
                        """
                        INSERT INTO key_property(layer_id, key_ref, config)
                        VALUES (?, ?, ?)
                        ON CONFLICT(layer_id, key_ref)
                        DO UPDATE SET config=excluded.config
                        """,
                        (layer_id, key_ref, source_property["config"]),
                    )
                plugins = conn.execute(
                    """
                    SELECT * FROM key_plugin
                    WHERE layer_id=? ORDER BY key_ref, position, id
                    """,
                    (layer_id,),
                ).fetchall()
                return jsonify([self._plugin(plugin) for plugin in plugins]), 201

        @app.post(
            "/api/layer/<int:layer_id>/keys/<key_ref>/move-to"
        )
        def move_key(layer_id, key_ref):
            data = request.get_json(silent=True) or {}
            destination_key_ref = str(
                data.get("destination_key_ref") or ""
            ).strip()
            if not destination_key_ref:
                return jsonify(error="missing destination_key_ref"), 400
            if destination_key_ref == key_ref:
                return jsonify(error="source and destination are identical"), 400

            with self.db.connect() as conn:
                layer = self._layer(conn, layer_id)
                if layer is None:
                    return jsonify(error="layer not found"), 404
                source_plugins = conn.execute(
                    """
                    SELECT * FROM key_plugin
                    WHERE layer_id=? AND key_ref=?
                    ORDER BY position, id
                    """,
                    (layer_id, key_ref),
                ).fetchall()
                if source_plugins:
                    conn.execute(
                        """
                        UPDATE key_plugin SET position=position + ?
                        WHERE layer_id=? AND key_ref=?
                        """,
                        (
                            len(source_plugins),
                            layer_id,
                            destination_key_ref,
                        ),
                    )
                    for position, plugin in enumerate(source_plugins):
                        conn.execute(
                            """
                            UPDATE key_plugin SET key_ref=?, position=?
                            WHERE id=?
                            """,
                            (destination_key_ref, position, plugin["id"]),
                        )
                source_property = conn.execute(
                    """
                    SELECT config FROM key_property
                    WHERE layer_id=? AND key_ref=?
                    """,
                    (layer_id, key_ref),
                ).fetchone()
                if source_property is not None:
                    conn.execute(
                        """
                        DELETE FROM key_property
                        WHERE layer_id=? AND key_ref=?
                        """,
                        (layer_id, destination_key_ref),
                    )
                    conn.execute(
                        """
                        UPDATE key_property SET key_ref=?
                        WHERE layer_id=? AND key_ref=?
                        """,
                        (destination_key_ref, layer_id, key_ref),
                    )
                return jsonify(self._item(conn, layer, True))

        @app.delete("/api/layer/<int:layer_id>/keys/<key_ref>")
        def clear_key(layer_id, key_ref):
            with self.db.connect() as conn:
                layer = self._layer(conn, layer_id)
                if layer is None:
                    return jsonify(error="layer not found"), 404
                key_plugins = conn.execute(
                    "SELECT * FROM key_plugin WHERE layer_id=? AND key_ref=?",
                    (layer_id, key_ref),
                ).fetchall()
                conn.execute(
                    "DELETE FROM key_plugin WHERE layer_id=? AND key_ref=?",
                    (layer_id, key_ref),
                )
                conn.execute(
                    "DELETE FROM key_property WHERE layer_id=? AND key_ref=?",
                    (layer_id, key_ref),
                )
                for plugin in key_plugins:
                    self._delete_plugin_media(conn, plugin)
                return jsonify(self._item(conn, layer, True))

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
                if row["plugin_id"] in self.MEDIA_PLUGIN_IDS:
                    self._delete_media(
                        conn,
                        previous_config,
                        keep=self._media_names(current_config),
                    )
                return jsonify(self._plugin(row))

        @app.put("/api/layer/<int:layer_id>/keys/<key_ref>/properties")
        def update_key_properties(layer_id, key_ref):
            data = request.get_json(silent=True) or {}
            try:
                config = json.dumps(data.get("config") or {})
            except (TypeError, ValueError):
                return jsonify(error="invalid config"), 400
            with self.db.connect() as conn:
                if self._layer(conn, layer_id) is None:
                    return jsonify(error="layer not found"), 404
                conn.execute(
                    """
                    INSERT INTO key_property(layer_id, key_ref, config)
                    VALUES (?, ?, ?)
                    ON CONFLICT(layer_id, key_ref)
                    DO UPDATE SET config=excluded.config
                    """,
                    (layer_id, key_ref, config),
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
                self._delete_plugin_media(conn, row)
                return jsonify(ok=True)

        @app.get("/api/layer/active")
        def active_layer():
            with self.db.connect() as conn:
                layer = conn.execute(
                    "SELECT * FROM layer WHERE active=1 LIMIT 1"
                ).fetchone()
                if layer is not None:
                    layout = self.layout_api.find(
                        conn,
                        layer["layout_id"],
                    )
                    return jsonify(
                        layer=self._item(conn, layer, True),
                        layout=self.layout_api.row_to_dict(layout),
                    )

                layout = self.layout_api.find_default(conn)
                if layout is None:
                    return jsonify(error="not found"), 404
                return jsonify(
                    layer=None,
                    layout=self.layout_api.row_to_dict(layout),
                )
