import io
import os
from pathlib import Path
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
    from kbrd_api.api.layer import Layer
    from kbrd_api.db import DB
except ModuleNotFoundError:
    create_app = None
    Layer = None
    DB = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class LayerApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.media_dir = tempfile.TemporaryDirectory()
        self.font_dir = tempfile.TemporaryDirectory()
        self.bundled_font_dir = tempfile.TemporaryDirectory()
        Path(self.font_dir.name, "Emoji.ttf").write_bytes(b"font-data")
        Path(self.font_dir.name, "ignored.txt").write_text("ignored")
        app, _ = create_app(Config(
            db_path=self.db_path,
            media_dir=self.media_dir.name,
            font_dir=self.font_dir.name,
            bundled_font_dir=self.bundled_font_dir.name,
        ))
        app.testing = True
        self.client = app.test_client()
        response = self.client.post("/api/layout", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [{"elements": [[{"name": "A", "size": 16}]]}],
        })
        self.layout = response.json

    def tearDown(self):
        os.unlink(self.db_path)
        self.media_dir.cleanup()
        self.font_dir.cleanup()
        self.bundled_font_dir.cleanup()

    def test_layer_plugins_and_active_payload(self):
        created = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Editing"},
        )
        self.assertEqual(created.status_code, 201)
        layer = created.json

        activated = self.client.put(
            f"/api/layer/{layer['id']}/activate"
        )
        self.assertTrue(activated.json["active"])

        plugin = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-label",
                "plugin_version": "1.0.0",
                "config": {"text": "Hello"},
            },
        )
        self.assertEqual(plugin.status_code, 201)
        self.assertEqual(plugin.json["position"], 0)

        updated = self.client.put(
            f"/api/key-plugin/{plugin.json['id']}",
            json={"enabled": False, "config": {"text": "World"}},
        )
        self.assertFalse(updated.json["enabled"])

        active = self.client.get("/api/layer/active")
        self.assertEqual(active.json["layout"]["id"], self.layout["id"])
        self.assertEqual(active.json["layer"]["plugins"][0]["config"], {
            "text": "World",
        })

        self.client.delete("/api/layer/active")
        active = self.client.get("/api/layer/active")
        self.assertIsNone(active.json["layer"])

    def test_saves_and_loads_factory_layout_per_layer(self):
        first = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "First"},
        ).json
        second = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Second"},
        ).json
        self.assertIsNone(first["factory_layout"])

        saved = self.client.put(
            f"/api/layer/{first['id']}/factory-layout",
            json={
                "factory_layout": {
                    "rowOverrides": {"0": [1, 2]},
                    "cells": {"1": {"typeId": "kbrd.layout-key", "unit": 1}},
                    "mergeGroups": [],
                },
            },
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            saved.json["factory_layout"]["rowOverrides"], {"0": [1, 2]}
        )

        # Each layer keeps its own disposition — the other layer on the
        # same layout is untouched.
        untouched = self.client.get("/api/layer").json
        by_id = {item["id"]: item for item in untouched}
        self.assertEqual(
            by_id[first["id"]]["factory_layout"]["cells"]["1"]["unit"], 1
        )
        self.assertIsNone(by_id[second["id"]]["factory_layout"])

        cleared = self.client.put(
            f"/api/layer/{first['id']}/factory-layout",
            json={"factory_layout": None},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json["factory_layout"])

    def test_factory_layout_rejects_invalid_payload_and_missing_layer(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Invalid layout"},
        ).json

        invalid = self.client.put(
            f"/api/layer/{layer['id']}/factory-layout",
            json={"factory_layout": "not an object"},
        )
        self.assertEqual(invalid.status_code, 400)

        missing = self.client.put(
            "/api/layer/999/factory-layout",
            json={"factory_layout": {}},
        )
        self.assertEqual(missing.status_code, 404)

    def test_layer_requires_an_existing_layout(self):
        response = self.client.post(
            "/api/layout/999/layer",
            json={"name": "Orphan"},
        )
        self.assertEqual(response.status_code, 404)

    def test_migrates_legacy_plugin_ids(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Legacy plugins"},
        ).json
        legacy_ids = (
            "kbrd.image",
            "kbrd.label",
            "kbrd.rectangle",
            "kbrd.send-keys",
            "kbrd.set-geometry",
            "kbrd.set-workspace",
            "kbrd.invoke-geometry",
            "kbrd.invoke-workspace",
        )
        for plugin_id in legacy_ids:
            response = self.client.post(
                f"/api/layer/{layer['id']}/keys/A/plugins",
                json={"plugin_id": plugin_id, "plugin_version": "1.0.0"},
            )
            self.assertEqual(response.status_code, 201)

        DB(self.db_path).init_schema()
        migrated = self.client.get(f"/api/layer/{layer['id']}").json
        self.assertEqual(
            [plugin["plugin_id"] for plugin in migrated["plugins"]],
            [
                "kbrd.render-image",
                "kbrd.render-label",
                "kbrd.render-rectangle",
                "kbrd.invoke-keystroke",
                # `kbrd.set-geometry`/`kbrd.set-workspace` and the ids they
                # were first migrated to (`kbrd.invoke-geometry`/
                # `kbrd.invoke-workspace`) both converge on the current
                # ids — a DB migrated once, twice, or never before all
                # land in the same place.
                "kbrd.invoke-layout",
                "kbrd.invoke-layer",
                "kbrd.invoke-layout",
                "kbrd.invoke-layer",
            ],
        )

    def test_lists_all_layers_and_layout_activation_clears_layer(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Action target"},
        ).json
        self.client.put(f"/api/layer/{layer['id']}/activate")
        listed = self.client.get("/api/layer")
        self.assertEqual([item["id"] for item in listed.json], [layer["id"]])

        other = self.client.post("/api/layout", json={
            "name": "Other",
            "unit": "px",
            "geometry": [],
        }).json
        self.client.put(f"/api/layout/{other['id']}/activate")
        active = self.client.get("/api/layer/active").json
        self.assertIsNone(active["layer"])
        self.assertEqual(active["layout"]["id"], other["id"])

    def test_updates_key_properties(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Styled"},
        ).json
        config = {
            "keyMode": "toggle",
            "borderEnabled": True,
            "downEnabled": True,
            "upBorderColor": "#ff0000",
            "downBorderColor": "#00ff00",
            "upBorderWidth": 3,
            "downBorderWidth": 4,
        }
        updated = self.client.put(
            f"/api/layer/{layer['id']}/keys/A/properties",
            json={"config": config},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json, {"key_ref": "A", "config": config})

        activated = self.client.put(
            f"/api/layer/{layer['id']}/activate"
        )
        self.assertEqual(
            activated.json["key_properties"],
            [{"key_ref": "A", "config": config}],
        )

    def test_duplicates_key_plugins_before_existing_instances(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Duplicated plugins"},
        ).json
        source = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-label",
                "plugin_version": "2.0.0",
                "config": {"text": "Source"},
            },
        ).json
        self.client.put(
            f"/api/key-plugin/{source['id']}",
            json={"enabled": False},
        )
        existing = self.client.post(
            f"/api/layer/{layer['id']}/keys/B/plugins",
            json={
                "plugin_id": "kbrd.render-rectangle",
                "plugin_version": "1.0.0",
                "config": {"color": "#123456"},
            },
        ).json

        response = self.client.post(
            f"/api/layer/{layer['id']}/keys/B/plugins/duplicate-from",
            json={"source_key_ref": "A"},
        )

        self.assertEqual(response.status_code, 201)
        target_plugins = [
            plugin for plugin in response.json if plugin["key_ref"] == "B"
        ]
        self.assertEqual(
            [plugin["plugin_id"] for plugin in target_plugins],
            ["kbrd.render-label", "kbrd.render-rectangle"],
        )
        self.assertEqual([plugin["position"] for plugin in target_plugins], [0, 1])
        self.assertEqual(target_plugins[0]["plugin_version"], "2.0.0")
        self.assertEqual(target_plugins[0]["config"], {"text": "Source"})
        self.assertFalse(target_plugins[0]["enabled"])
        self.assertEqual(target_plugins[1]["id"], existing["id"])

    def test_clears_all_key_plugins_and_properties(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Cleared key"},
        ).json
        self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-label",
                "plugin_version": "1.0.0",
                "config": {"text": "Remove me"},
            },
        )
        self.client.post(
            f"/api/layer/{layer['id']}/keys/B/plugins",
            json={
                "plugin_id": "kbrd.render-label",
                "plugin_version": "1.0.0",
                "config": {"text": "Keep me"},
            },
        )
        self.client.put(
            f"/api/layer/{layer['id']}/keys/A/properties",
            json={"config": {"keyMode": "toggle"}},
        )

        response = self.client.delete(
            f"/api/layer/{layer['id']}/keys/A"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [plugin["key_ref"] for plugin in response.json["plugins"]],
            ["B"],
        )
        self.assertEqual(response.json["key_properties"], [])

    def test_moves_key_plugins_and_properties_before_destination_plugins(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Moved key"},
        ).json
        moved = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-label",
                "plugin_version": "1.0.0",
                "config": {"text": "Moved"},
            },
        ).json
        existing = self.client.post(
            f"/api/layer/{layer['id']}/keys/B/plugins",
            json={
                "plugin_id": "kbrd.render-rectangle",
                "plugin_version": "1.0.0",
                "config": {"color": "#123456"},
            },
        ).json
        self.client.put(
            f"/api/layer/{layer['id']}/keys/A/properties",
            json={"config": {"keyMode": "toggle"}},
        )
        self.client.put(
            f"/api/layer/{layer['id']}/keys/B/properties",
            json={"config": {"keyMode": "momentary"}},
        )

        response = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/move-to",
            json={"destination_key_ref": "B"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                (plugin["id"], plugin["key_ref"], plugin["position"])
                for plugin in response.json["plugins"]
            ],
            [(moved["id"], "B", 0), (existing["id"], "B", 1)],
        )
        self.assertEqual(
            response.json["key_properties"],
            [{"key_ref": "B", "config": {"keyMode": "toggle"}}],
        )

    def test_image_upload_is_deleted_with_plugin(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Images"},
        ).json
        uploaded = self.client.post(
            "/api/media",
            data={
                "file": (
                    io.BytesIO(
                        b"\xff\xd8\xff"
                        + b"\x00" * (30 * 1024 - 5)
                        + b"\xff\xd9"
                    ),
                    "photo.jpeg",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        filename = uploaded.json["filename"]
        media_path = Path(self.media_dir.name) / filename
        self.assertTrue(media_path.is_file())
        self.assertEqual(media_path.stat().st_size, 30 * 1024)

        plugin = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-image",
                "plugin_version": "1.0.0",
                "config": {"media": filename, "fullSize": True, "size": 75},
            },
        ).json
        response = self.client.delete(f"/api/key-plugin/{plugin['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(media_path.exists())

    def test_image_media_names_include_down_state(self):
        self.assertEqual(
            Layer._media_names({
                "media": "up.jpeg",
                "down": {
                    "enabled": True,
                    "delay": 25,
                    "config": {"media": "down.jpeg"},
                },
            }),
            {"up.jpeg", "down.jpeg"},
        )

    def test_video_upload_is_deleted_with_plugin(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Videos"},
        ).json
        uploaded = self.client.post(
            "/api/media",
            data={
                "file": (
                    io.BytesIO(b"\x1aE\xdf\xa3" + b"\x00" * 128),
                    "transparent.webm",
                    "video/webm",
                )
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        filename = uploaded.json["filename"]
        media_path = Path(self.media_dir.name) / filename
        self.assertTrue(media_path.is_file())

        plugin = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-video",
                "plugin_version": "1.0.0",
                "config": {"media": filename, "fit": "contain"},
            },
        ).json
        response = self.client.delete(f"/api/key-plugin/{plugin['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(media_path.exists())

    def test_replacing_video_deletes_previous_media(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Replace video"},
        ).json
        old_media = "old.webm"
        new_media = "new.webm"
        old_path = Path(self.media_dir.name) / old_media
        new_path = Path(self.media_dir.name) / new_media
        old_path.write_bytes(b"old")
        new_path.write_bytes(b"new")
        plugin = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-video",
                "plugin_version": "1.0.0",
                "config": {"media": old_media},
            },
        ).json

        response = self.client.put(
            f"/api/key-plugin/{plugin['id']}",
            json={"config": {"media": new_media}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(old_path.exists())
        self.assertTrue(new_path.exists())

    def test_shared_media_is_deleted_only_after_last_reference(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Shared video"},
        ).json
        filename = "shared.webm"
        media_path = Path(self.media_dir.name) / filename
        media_path.write_bytes(b"shared")
        plugins = [
            self.client.post(
                f"/api/layer/{layer['id']}/keys/{key}/plugins",
                json={
                    "plugin_id": "kbrd.render-video",
                    "plugin_version": "1.0.0",
                    "config": {"media": filename},
                },
            ).json
            for key in ("A", "B")
        ]

        self.client.delete(f"/api/key-plugin/{plugins[0]['id']}")
        self.assertTrue(media_path.exists())

        self.client.delete(f"/api/key-plugin/{plugins[1]['id']}")
        self.assertFalse(media_path.exists())

    def test_rejects_unsupported_video_container(self):
        response = self.client.post(
            "/api/media",
            data={
                "file": (
                    io.BytesIO(b"not-a-video"),
                    "movie.mov",
                    "video/quicktime",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json, {"error": "invalid media"})

    def test_lists_and_serves_data_fonts(self):
        listed = self.client.get("/api/fonts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json, [{
            "label": "Emoji",
            "value": "Emoji.ttf",
        }])

        with self.client.get("/api/fonts/Emoji.ttf") as font:
            self.assertEqual(font.status_code, 200)
            self.assertEqual(font.data, b"font-data")


if __name__ == "__main__":
    unittest.main()
