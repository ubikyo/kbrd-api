import io
import os
from pathlib import Path
import tempfile
import unittest

from kbrd_api.config import Config
from tests.test_fonts import font as sfnt, WINDOWS_ENGLISH

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
        self.assertIn(layer["id"], [item["id"] for item in listed.json])

        other = self.client.post("/api/layout", json={
            "name": "Other",
            "unit": "px",
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

    def test_duplicate_from_also_copies_key_property(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Duplicated property"},
        ).json
        self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={"plugin_id": "kbrd.render-label", "config": {"text": "Source"}},
        )
        self.client.put(
            f"/api/layer/{layer['id']}/keys/A/properties",
            json={"config": {"keyMode": "toggle"}},
        )

        response = self.client.post(
            f"/api/layer/{layer['id']}/keys/B/plugins/duplicate-from",
            json={"source_key_ref": "A"},
        )
        self.assertEqual(response.status_code, 201)

        activated = self.client.put(f"/api/layer/{layer['id']}/activate")
        self.assertEqual(
            sorted(activated.json["key_properties"], key=lambda item: item["key_ref"]),
            [
                {"key_ref": "A", "config": {"keyMode": "toggle"}},
                {"key_ref": "B", "config": {"keyMode": "toggle"}},
            ],
        )

    def test_duplicate_layer_clones_factory_layout_plugins_and_properties(self):
        layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Original"},
        ).json
        self.client.put(
            f"/api/layer/{layer['id']}/factory-layout",
            json={"factory_layout": {"rowOverrides": {}, "cells": {}, "mergeGroups": []}},
        )
        self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={"plugin_id": "kbrd.render-label", "config": {"text": "Source"}},
        )
        self.client.put(
            f"/api/layer/{layer['id']}/keys/A/properties",
            json={"config": {"keyMode": "toggle"}},
        )

        response = self.client.post(
            f"/api/layer/{layer['id']}/duplicate",
            json={"name": "Copy", "description": "A copy"},
        )

        self.assertEqual(response.status_code, 201)
        clone = response.json
        self.assertNotEqual(clone["id"], layer["id"])
        self.assertEqual(clone["name"], "Copy")
        self.assertEqual(clone["layout_id"], layer["layout_id"])
        self.assertEqual(
            clone["factory_layout"],
            {"rowOverrides": {}, "cells": {}, "mergeGroups": []},
        )
        self.assertEqual([plugin["key_ref"] for plugin in clone["plugins"]], ["A"])
        self.assertEqual(clone["plugins"][0]["config"], {"text": "Source"})
        self.assertEqual(
            clone["key_properties"], [{"key_ref": "A", "config": {"keyMode": "toggle"}}]
        )

        # Editing the clone must never touch the source — independent rows,
        # not shared references.
        self.client.put(
            f"/api/key-plugin/{clone['plugins'][0]['id']}",
            json={"config": {"text": "Edited"}},
        )
        layers = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        source_after = next(item for item in layers if item["id"] == layer["id"])
        self.assertEqual(source_after["plugins"][0]["config"], {"text": "Source"})

    def test_replace_layer_overwrites_target_content(self):
        source = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Source"},
        ).json
        self.client.post(
            f"/api/layer/{source['id']}/keys/A/plugins",
            json={"plugin_id": "kbrd.render-label", "config": {"text": "Fresh"}},
        )
        target = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Target"},
        ).json
        self.client.post(
            f"/api/layer/{target['id']}/keys/B/plugins",
            json={"plugin_id": "kbrd.render-rectangle", "config": {"color": "#000"}},
        )

        response = self.client.post(
            f"/api/layer/{target['id']}/replace",
            json={"source_id": source["id"]},
        )

        self.assertEqual(response.status_code, 200)
        replaced = response.json
        self.assertEqual(replaced["id"], target["id"])
        self.assertEqual(replaced["name"], "Target")
        self.assertEqual([plugin["key_ref"] for plugin in replaced["plugins"]], ["A"])
        self.assertEqual(replaced["plugins"][0]["config"], {"text": "Fresh"})
        # Source untouched.
        layers = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        source_after = next(item for item in layers if item["id"] == source["id"])
        self.assertEqual([plugin["key_ref"] for plugin in source_after["plugins"]], ["A"])

    def test_duplicate_layout_clones_settings_and_every_layer(self):
        layer_a = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Layer A"},
        ).json
        self.client.post(
            f"/api/layer/{layer_a['id']}/keys/A/plugins",
            json={"plugin_id": "kbrd.render-label", "config": {"text": "A"}},
        )
        self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Layer B"},
        )

        response = self.client.post(
            f"/api/layout/{self.layout['id']}/duplicate",
            json={"name": "Layout copy"},
        )

        self.assertEqual(response.status_code, 201)
        clone = response.json
        self.assertNotEqual(clone["id"], self.layout["id"])
        self.assertEqual(clone["name"], "Layout copy")
        self.assertEqual(clone["unit_mm"], self.layout["unit_mm"])
        self.assertFalse(clone["active"])

        clone_layers = self.client.get(f"/api/layout/{clone['id']}/layer").json
        self.assertEqual(
            sorted(layer["name"] for layer in clone_layers),
            ["Layer A", "Layer B"],
        )
        cloned_layer_a = next(l for l in clone_layers if l["name"] == "Layer A")
        self.assertEqual(
            [plugin["key_ref"] for plugin in cloned_layer_a["plugins"]], ["A"]
        )

    def test_replace_layout_overwrites_settings_and_layers(self):
        source_layout = self.client.post("/api/layout", json={
            "name": "Source layout",
            "unit": "mm",
            # Distinct from `self.layout`'s own default (19.05, from
            # `setUp`) so the assertion below actually proves the target's
            # settings got overwritten, not just left already-equal.
            "unit_mm": 24,
        }).json
        source_layer = self.client.post(
            f"/api/layout/{source_layout['id']}/layer",
            json={"name": "Source layer"},
        ).json
        self.client.post(
            f"/api/layer/{source_layer['id']}/keys/X/plugins",
            json={"plugin_id": "kbrd.render-label", "config": {"text": "X"}},
        )
        target_layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Target layer"},
        ).json

        response = self.client.post(
            f"/api/layout/{self.layout['id']}/replace",
            json={"source_id": source_layout["id"]},
        )

        self.assertEqual(response.status_code, 200)
        replaced = response.json
        self.assertEqual(replaced["id"], self.layout["id"])
        self.assertEqual(replaced["unit_mm"], 24)

        layers = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        self.assertEqual([layer["name"] for layer in layers], ["Source layer"])
        source_layer_after = next(l for l in layers if l["name"] == "Source layer")
        self.assertEqual(
            [plugin["key_ref"] for plugin in source_layer_after["plugins"]], ["X"]
        )
        # The target's own previous layers are really gone, not just hidden.
        self.assertIsNone(
            next((l for l in layers if l["id"] == target_layer["id"]), None)
        )

    def test_creating_a_layout_creates_no_layer(self):
        # A fresh layout starts empty — Layer mode's own empty state is
        # what offers to add the first layer (see `Layout._write`).
        layers = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        self.assertEqual(layers, [])

    def test_cannot_delete_the_last_layer_but_can_delete_any_other(self):
        # Once a layout has a layer, it must keep one — deleting it while
        # it's the only one must be rejected.
        default_layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Default"},
        ).json
        rejected = self.client.delete(f"/api/layer/{default_layer['id']}")
        self.assertEqual(rejected.status_code, 400)
        still_there = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        self.assertEqual([layer["id"] for layer in still_there], [default_layer["id"]])

        # A second layer makes either one deletable again.
        second_layer = self.client.post(
            f"/api/layout/{self.layout['id']}/layer",
            json={"name": "Second"},
        ).json
        allowed = self.client.delete(f"/api/layer/{default_layer['id']}")
        self.assertEqual(allowed.status_code, 200)
        remaining = self.client.get(f"/api/layout/{self.layout['id']}/layer").json
        self.assertEqual([layer["id"] for layer in remaining], [second_layer["id"]])

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

    def _upload(self, filename, mimetype):
        return self.client.post(
            "/api/media",
            data={"file": (io.BytesIO(b"not-really-a-media"), filename, mimetype)},
            content_type="multipart/form-data",
        )

    def test_accepts_every_video_container_on_the_list(self):
        for filename, mimetype in (
            ("clip.mp4", "video/mp4"),
            ("clip.m4v", "video/x-m4v"),
            ("clip.mov", "video/quicktime"),
            ("clip.mkv", "video/x-matroska"),
            ("clip.webm", "video/webm"),
            ("clip.avi", "video/x-msvideo"),
        ):
            with self.subTest(filename=filename):
                self.assertEqual(self._upload(filename, mimetype).status_code, 201)

    def test_rejects_containers_without_a_native_demuxer(self):
        # ffmpeg could demux every one of these and `gst1-libav` would
        # register a fallback element for it, but the device has no native
        # GStreamer demuxer — see `Layer`'s own note.
        for filename, mimetype in (
            ("clip.m2ts", "video/mp2t"),
            ("clip.mts", ""),
            ("clip.mpg", "video/mpeg"),
            ("clip.wmv", "video/x-ms-wmv"),
            ("clip.flv", "video/x-flv"),
            ("clip.gif", "image/gif"),
        ):
            with self.subTest(filename=filename):
                self.assertEqual(self._upload(filename, mimetype).status_code, 400)

    def test_accepts_a_video_whose_type_the_browser_could_not_name(self):
        # Whether a browser can name `.mkv` or `.avi` depends on the
        # machine it runs on; one that can't sends nothing (see `Layer`'s
        # own `AMBIGUOUS_VIDEO_MIMETYPES`).
        for filename, mimetype in (
            ("clip.mkv", ""),
            ("clip.avi", "application/octet-stream"),
        ):
            with self.subTest(filename=filename):
                self.assertEqual(self._upload(filename, mimetype).status_code, 201)

    def test_that_leniency_does_not_reach_images(self):
        # An image still has to declare itself one: nothing about PNG or
        # JPEG is ambiguous to a browser.
        self.assertEqual(self._upload("art.png", "").status_code, 400)

    def test_rejects_images_outside_png_and_jpeg(self):
        # The device could draw BMP, and could never draw WebP — neither
        # is on the list either way.
        for filename, mimetype in (
            ("art.bmp", "image/bmp"),
            ("art.webp", "image/webp"),
        ):
            with self.subTest(filename=filename):
                response = self._upload(filename, mimetype)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json, {"error": "invalid media"})

    def test_an_uploaded_media_can_be_read_back_from_a_relative_dir(self):
        # Regression: an upload is written with a plain filesystem path,
        # but `send_from_directory` resolves a relative one against the
        # application root rather than the working directory — configured
        # relatively (as `dev.sh` does), every stored file came back 404.
        # The directory therefore has to sit under the working directory
        # for this to say anything at all.
        with tempfile.TemporaryDirectory(dir=".") as media_dir:
            relative = os.path.relpath(media_dir)
            self.assertFalse(os.path.isabs(relative))

            app, _ = create_app(
                Config(
                    db_path=self.db_path,
                    media_dir=relative,
                    font_dir=relative,
                )
            )
            app.testing = True
            client = app.test_client()

            stored = client.post(
                "/api/media",
                data={"file": (io.BytesIO(b"\x89PNG-ish"), "art.png", "image/png")},
                content_type="multipart/form-data",
            )
            self.assertEqual(stored.status_code, 201)

            read_back = client.get(f"/api/media/{stored.json['filename']}")
            self.assertEqual(read_back.status_code, 200)
            self.assertEqual(read_back.data, b"\x89PNG-ish")

    def test_rejects_unsupported_video_container(self):
        # Ogg is the example now that `.mov` is accepted: the device
        # carries no Ogg demuxer in its GStreamer plugin set.
        response = self.client.post(
            "/api/media",
            data={
                "file": (
                    io.BytesIO(b"not-a-video"),
                    "movie.ogv",
                    "video/ogg",
                )
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json, {"error": "invalid media"})

    def test_lists_and_serves_data_fonts(self):
        # Two files of one family, neither of which a filename could be
        # split into it: `Teko-Light` says "Teko Light" in the name its
        # older records carry, and only its typographic pair says the
        # family is "Teko" — see `kbrd_api/fonts.py`.
        Path(self.bundled_font_dir.name, "Teko-Bold.ttf").write_bytes(
            sfnt({
                (*WINDOWS_ENGLISH, 1): "Teko",
                (*WINDOWS_ENGLISH, 2): "Bold",
            }, weight=700)
        )
        Path(self.bundled_font_dir.name, "Teko-Light.ttf").write_bytes(
            sfnt({
                (*WINDOWS_ENGLISH, 1): "Teko Light",
                (*WINDOWS_ENGLISH, 2): "Regular",
                (*WINDOWS_ENGLISH, 16): "Teko",
                (*WINDOWS_ENGLISH, 17): "Light",
            }, weight=300)
        )

        listed = self.client.get("/api/fonts")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json, [
            # `Emoji.ttf` holds nine bytes that are not a font. It is
            # still listed, named after itself, with the empty style the
            # editors show as "Regular".
            {
                "value": "Emoji.ttf",
                "label": "Emoji",
                "family": "Emoji",
                "style": "",
            },
            # Light before Bold: a family is ordered by weight, not
            # alphabetically, which would open it on the wrong end.
            {
                "value": "Teko-Light.ttf",
                "label": "Teko Light",
                "family": "Teko",
                "style": "Light",
            },
            {
                "value": "Teko-Bold.ttf",
                "label": "Teko Bold",
                "family": "Teko",
                "style": "Bold",
            },
        ])

        with self.client.get("/api/fonts/Emoji.ttf") as font:
            self.assertEqual(font.status_code, 200)
            self.assertEqual(font.data, b"font-data")

    def test_names_an_uploaded_font_from_the_file_that_is_served(self):
        # Same filename in both directories: the upload is what
        # `/api/fonts/<filename>` hands back, so it has to be what the
        # listing read the family off too.
        Path(self.bundled_font_dir.name, "Face.ttf").write_bytes(
            sfnt({(*WINDOWS_ENGLISH, 1): "Bundled"})
        )
        Path(self.font_dir.name, "Face.ttf").write_bytes(
            sfnt({(*WINDOWS_ENGLISH, 1): "Uploaded"})
        )

        listed = self.client.get("/api/fonts")

        self.assertEqual(
            [entry["family"] for entry in listed.json if entry["value"] == "Face.ttf"],
            ["Uploaded"],
        )


if __name__ == "__main__":
    unittest.main()
