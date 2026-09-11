import io
import os
import tempfile
from pathlib import Path
import unittest

from kbrd_api.config import Config
from kbrd_api.db import DB

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class MediaCategoryApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        # An upload filed in the library writes a real file, so this needs
        # somewhere to write it that isn't the device's own `/data/media`.
        self.media_dir = tempfile.TemporaryDirectory()
        app, _ = create_app(
            Config(db_path=self.db_path, media_dir=self.media_dir.name)
        )
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)
        self.media_dir.cleanup()

    def test_starts_with_a_default_category(self):
        response = self.client.get("/api/media-category")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json], ["Default"])

    def test_the_seeded_category_is_not_put_back_once_renamed(self):
        seeded = self.client.get("/api/media-category").json[0]
        self.client.put(f"/api/media-category/{seeded['id']}", json={"name": "Icons"})

        # What a restart does — the schema is initialised again over the
        # same database.
        DB(self.db_path).init_schema()
        listed = self.client.get("/api/media-category").json
        self.assertEqual([item["name"] for item in listed], ["Icons"])

    def test_create_trims_the_name_and_lists_it_back(self):
        created = self.client.post("/api/media-category", json={"name": "  Icons  "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["name"], "Icons")

        listed = self.client.get("/api/media-category").json
        self.assertEqual([item["name"] for item in listed], ["Default", "Icons"])

    def test_lists_by_name(self):
        for name in ("Wallpapers", "Icons", "Logos"):
            self.client.post("/api/media-category", json={"name": name})

        listed = self.client.get("/api/media-category").json
        self.assertEqual(
            [item["name"] for item in listed],
            ["Default", "Icons", "Logos", "Wallpapers"],
        )

    def test_rejects_a_missing_or_empty_name(self):
        for body in ({}, {"name": ""}, {"name": "   "}):
            response = self.client.post("/api/media-category", json=body)
            self.assertEqual(response.status_code, 400)

    def test_rejects_a_duplicate_name(self):
        self.client.post("/api/media-category", json={"name": "Icons"})
        again = self.client.post("/api/media-category", json={"name": "Icons"})
        self.assertEqual(again.status_code, 409)

    def test_rename(self):
        created = self.client.post("/api/media-category", json={"name": "Icons"}).json
        renamed = self.client.put(
            f"/api/media-category/{created['id']}", json={"name": "Glyphs"}
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json["name"], "Glyphs")

        listed = self.client.get("/api/media-category").json
        self.assertEqual([item["name"] for item in listed], ["Default", "Glyphs"])

    def test_rename_onto_another_category_is_refused(self):
        icons = self.client.post("/api/media-category", json={"name": "Icons"}).json
        self.client.post("/api/media-category", json={"name": "Logos"})

        response = self.client.put(
            f"/api/media-category/{icons['id']}", json={"name": "Logos"}
        )
        self.assertEqual(response.status_code, 409)
        # The refused write left the row alone.
        listed = self.client.get("/api/media-category").json
        self.assertEqual(
            [item["name"] for item in listed], ["Default", "Icons", "Logos"]
        )

    def test_rename_keeps_its_own_name_available(self):
        created = self.client.post("/api/media-category", json={"name": "Icons"}).json
        response = self.client.put(
            f"/api/media-category/{created['id']}", json={"name": "Icons"}
        )
        self.assertEqual(response.status_code, 200)

    def test_delete(self):
        created = self.client.post("/api/media-category", json={"name": "Icons"}).json
        deleted = self.client.delete(f"/api/media-category/{created['id']}")
        self.assertEqual(deleted.status_code, 200)
        listed = self.client.get("/api/media-category").json
        self.assertEqual([item["name"] for item in listed], ["Default"])

    def test_deleting_the_last_category_is_refused(self):
        only = self.client.get("/api/media-category").json[0]
        response = self.client.delete(f"/api/media-category/{only['id']}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.client.get("/api/media-category").json), 1)

    def test_the_last_category_is_whichever_one_is_left(self):
        # Not the seeded one specifically: delete that, and the one that
        # remains becomes undeletable in its turn.
        seeded = self.client.get("/api/media-category").json[0]
        icons = self.client.post("/api/media-category", json={"name": "Icons"}).json

        self.assertEqual(
            self.client.delete(f"/api/media-category/{seeded['id']}").status_code, 200
        )
        self.assertEqual(
            self.client.delete(f"/api/media-category/{icons['id']}").status_code, 400
        )

    def _file(self, name="art.png", mimetype="image/png"):
        return {"file": (io.BytesIO(b"\x89PNG-ish"), name, mimetype)}

    def test_an_upload_with_a_category_is_filed_in_the_library(self):
        category = self.client.get("/api/media-category").json[0]

        stored = self.client.post(
            "/api/media",
            data={**self._file("logo.png"), "category_id": str(category["id"])},
            content_type="multipart/form-data",
        )
        self.assertEqual(stored.status_code, 201)
        media = stored.json
        self.assertEqual(media["kind"], "photo")
        self.assertEqual(media["category_id"], category["id"])
        # The name it was dropped under, and the generated one it is kept
        # as — the two the panel needs.
        self.assertEqual(media["name"], "logo.png")
        self.assertTrue(media["filename"].endswith(".png"))
        self.assertNotEqual(media["filename"], media["name"])

        listed = self.client.get("/api/media").json
        self.assertEqual([item["id"] for item in listed], [media["id"]])

    def test_an_upload_without_a_category_stays_out_of_the_library(self):
        # What the plugin editors do — they only ever wanted a file store,
        # and still read the same `{filename}` back.
        stored = self.client.post(
            "/api/media",
            data=self._file(),
            content_type="multipart/form-data",
        )
        self.assertEqual(stored.status_code, 201)
        self.assertEqual(list(stored.json), ["filename"])
        self.assertEqual(self.client.get("/api/media").json, [])

    def test_an_upload_under_an_unknown_category_is_refused(self):
        response = self.client.post(
            "/api/media",
            data={**self._file(), "category_id": "999"},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/media").json, [])

    def test_deleting_a_category_takes_its_medias_and_their_files(self):
        category = self.client.get("/api/media-category").json[0]
        other = self.client.post(
            "/api/media-category", json={"name": "Icons"}
        ).json
        filename = self.client.post(
            "/api/media",
            data={**self._file(), "category_id": str(category["id"])},
            content_type="multipart/form-data",
        ).json["filename"]
        kept = self.client.post(
            "/api/media",
            data={
                **self._file("other.png"),
                "category_id": str(other["id"]),
            },
            content_type="multipart/form-data",
        ).json["filename"]
        media_path = Path(self.media_dir.name) / filename

        deleted = self.client.delete(f"/api/media-category/{category['id']}")
        self.assertEqual(deleted.status_code, 200)

        # Its own media and its file are gone; the other category's are
        # untouched.
        self.assertEqual(
            [item["filename"] for item in self.client.get("/api/media").json],
            [kept],
        )
        self.assertFalse(media_path.exists())
        self.assertTrue((Path(self.media_dir.name) / kept).is_file())

    def test_a_cascaded_media_keeps_its_file_while_a_key_still_draws_with_it(self):
        # The cascade drops the library rows, but a file a plugin config
        # still points at outlives them — the key would lose its artwork
        # otherwise.
        category = self.client.get("/api/media-category").json[0]
        self.client.post("/api/media-category", json={"name": "Icons"})
        filename = self.client.post(
            "/api/media",
            data={**self._file(), "category_id": str(category["id"])},
            content_type="multipart/form-data",
        ).json["filename"]

        layout = self.client.post(
            "/api/layout", json={"name": "Default", "unit": "mm"}
        ).json
        layer = self.client.get(f"/api/layout/{layout['id']}/layer").json[0]
        self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-image",
                "plugin_version": "1.0.0",
                "config": {"media": filename},
            },
        )

        self.client.delete(f"/api/media-category/{category['id']}")

        self.assertEqual(self.client.get("/api/media").json, [])
        self.assertTrue((Path(self.media_dir.name) / filename).is_file())

    def test_a_library_media_survives_the_plugin_that_used_it(self):
        # The collector counts references in plugin configs; a library row
        # is one too. Without that, attaching a dropped media to a key and
        # then clearing the key would delete the file under the library.
        category = self.client.get("/api/media-category").json[0]
        filename = self.client.post(
            "/api/media",
            data={**self._file(), "category_id": str(category["id"])},
            content_type="multipart/form-data",
        ).json["filename"]
        media_path = Path(self.media_dir.name) / filename
        self.assertTrue(media_path.is_file())

        layout = self.client.post(
            "/api/layout", json={"name": "Default", "unit": "mm"}
        ).json
        layer = self.client.get(f"/api/layout/{layout['id']}/layer").json[0]
        plugin = self.client.post(
            f"/api/layer/{layer['id']}/keys/A/plugins",
            json={
                "plugin_id": "kbrd.render-image",
                "plugin_version": "1.0.0",
                "config": {"media": filename},
            },
        ).json
        self.client.delete(f"/api/key-plugin/{plugin['id']}")

        self.assertTrue(media_path.is_file())
        self.assertEqual(len(self.client.get("/api/media").json), 1)

    def test_unknown_category(self):
        self.assertEqual(
            self.client.put("/api/media-category/999", json={"name": "Icons"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/media-category/999").status_code, 404)
