import io
import os
from pathlib import Path
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
    from kbrd_api.api.workspace import Workspace
except ModuleNotFoundError:
    create_app = None
    Workspace = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class WorkspaceApiTest(unittest.TestCase):
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
        response = self.client.post("/api/geometry", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [{"elements": [[{"name": "A", "size": 16}]]}],
        })
        self.geometry = response.json

    def tearDown(self):
        os.unlink(self.db_path)
        self.media_dir.cleanup()
        self.font_dir.cleanup()
        self.bundled_font_dir.cleanup()

    def test_workspace_plugins_and_active_payload(self):
        created = self.client.post(
            f"/api/geometry/{self.geometry['id']}/workspace",
            json={"name": "Editing"},
        )
        self.assertEqual(created.status_code, 201)
        workspace = created.json

        activated = self.client.put(
            f"/api/workspace/{workspace['id']}/activate"
        )
        self.assertTrue(activated.json["active"])

        plugin = self.client.post(
            f"/api/workspace/{workspace['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.label",
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

        active = self.client.get("/api/workspace/active")
        self.assertEqual(active.json["geometry"]["id"], self.geometry["id"])
        self.assertEqual(active.json["workspace"]["plugins"][0]["config"], {
            "text": "World",
        })

        self.client.delete("/api/workspace/active")
        active = self.client.get("/api/workspace/active")
        self.assertIsNone(active.json["workspace"])

    def test_workspace_requires_an_existing_geometry(self):
        response = self.client.post(
            "/api/geometry/999/workspace",
            json={"name": "Orphan"},
        )
        self.assertEqual(response.status_code, 404)

    def test_lists_all_workspaces_and_geometry_activation_clears_workspace(self):
        workspace = self.client.post(
            f"/api/geometry/{self.geometry['id']}/workspace",
            json={"name": "Action target"},
        ).json
        self.client.put(f"/api/workspace/{workspace['id']}/activate")
        listed = self.client.get("/api/workspace")
        self.assertEqual([item["id"] for item in listed.json], [workspace["id"]])

        other = self.client.post("/api/geometry", json={
            "name": "Other",
            "unit": "px",
            "geometry": [],
        }).json
        self.client.put(f"/api/geometry/{other['id']}/activate")
        active = self.client.get("/api/workspace/active").json
        self.assertIsNone(active["workspace"])
        self.assertEqual(active["geometry"]["id"], other["id"])

    def test_updates_key_properties(self):
        workspace = self.client.post(
            f"/api/geometry/{self.geometry['id']}/workspace",
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
            f"/api/workspace/{workspace['id']}/keys/A/properties",
            json={"config": config},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json, {"key_ref": "A", "config": config})

        activated = self.client.put(
            f"/api/workspace/{workspace['id']}/activate"
        )
        self.assertEqual(
            activated.json["key_properties"],
            [{"key_ref": "A", "config": config}],
        )

    def test_image_upload_is_deleted_with_plugin(self):
        workspace = self.client.post(
            f"/api/geometry/{self.geometry['id']}/workspace",
            json={"name": "Images"},
        ).json
        uploaded = self.client.post(
            "/api/medias",
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
            f"/api/workspace/{workspace['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.image",
                "plugin_version": "1.0.0",
                "config": {"media": filename, "fullSize": True, "size": 75},
            },
        ).json
        response = self.client.delete(f"/api/key-plugin/{plugin['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(media_path.exists())

    def test_image_media_names_include_down_state(self):
        self.assertEqual(
            Workspace._media_names({
                "media": "up.jpeg",
                "down": {
                    "enabled": True,
                    "delay": 25,
                    "config": {"media": "down.jpeg"},
                },
            }),
            {"up.jpeg", "down.jpeg"},
        )

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
