import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class BackupApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.media_dir = tempfile.TemporaryDirectory()
        app, _ = create_app(
            Config(db_path=self.db_path, media_dir=self.media_dir.name)
        )
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)
        self.media_dir.cleanup()

    # -- the device, as the tests set it up and read it back ------------

    def _add_media(self, name="art.png"):
        """One media through the API, so the library row and the file on
        disk are both there — what a backup has to carry both halves of."""
        category = self.client.get("/api/media-category").json[0]
        stored = self.client.post(
            "/api/media",
            data={
                "file": (io.BytesIO(b"\x89PNG-ish"), name, "image/png"),
                "category_id": str(category["id"]),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(stored.status_code, 201)
        return stored.json

    def _media_files(self):
        return sorted(
            entry.name
            for entry in Path(self.media_dir.name).iterdir()
            if entry.is_file()
        )

    def _download(self) -> bytes:
        response = self.client.get("/api/backup")
        self.assertEqual(response.status_code, 200)
        return response.get_data()

    def _upload(self, data: bytes, filename="kbrd-backup.zip"):
        return self.client.post(
            "/api/backup/restore",
            data={"file": (io.BytesIO(data), filename)},
            content_type="multipart/form-data",
        )

    # -- archives the tests build by hand -------------------------------

    @staticmethod
    def _archive(entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    @staticmethod
    def _database(script: str) -> bytes:
        """A database built from `script`, as bytes to put in an archive."""
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(path)
            conn.executescript(script)
            conn.commit()
            conn.close()
            with open(path, "rb") as file:
                return file.read()
        finally:
            os.unlink(path)

    LEGACY_SCHEMA = """
        CREATE TABLE geometry (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          name        TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          author      TEXT NOT NULL DEFAULT '',
          unit        TEXT NOT NULL CHECK(unit IN ('px', 'mm')),
          geometry    TEXT NOT NULL,
          svg         TEXT NOT NULL DEFAULT '',
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE workspace (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          geometry_id INTEGER NOT NULL
            REFERENCES geometry(id) ON DELETE CASCADE,
          name        TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          active      INTEGER NOT NULL DEFAULT 0,
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO geometry(name, unit, geometry)
        VALUES ('Old layout', 'mm', '[]');
        INSERT INTO workspace(geometry_id, name) VALUES (1, 'Old layer');
    """

    # -- backup ---------------------------------------------------------

    def test_a_backup_downloads_as_a_named_archive(self):
        response = self.client.get("/api/backup")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.get_data())))
        disposition = response.headers["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertRegex(disposition, r"kbrd-backup-\d{8}-\d{6}\.zip")

    def test_a_backup_holds_the_database_and_every_media_file(self):
        self.client.post("/api/layout", json={"name": "Taken", "unit": "mm"})
        media = self._add_media()

        with zipfile.ZipFile(io.BytesIO(self._download())) as archive:
            names = sorted(archive.namelist())
            database = archive.read("database.db")
            stored = archive.read(f"media/{media['filename']}")

        self.assertEqual(names, ["database.db", f"media/{media['filename']}"])
        self.assertEqual(stored, b"\x89PNG-ish")
        # The database in it is a real one, holding what was there when it
        # was taken — read on its own, as a restore elsewhere would.
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            with open(path, "wb") as file:
                file.write(database)
            conn = sqlite3.connect(path)
            names = [row[0] for row in conn.execute("SELECT name FROM layout")]
            conn.close()
        finally:
            os.unlink(path)
        self.assertEqual(names, ["Taken"])

    # -- restore --------------------------------------------------------

    def test_a_restore_puts_back_the_database_and_the_media_library(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})
        kept = self._add_media("kept.png")
        backup = self._download()

        # Everything that happened after the backup was taken is gone,
        # the library included.
        self.client.post("/api/layout", json={"name": "Later", "unit": "mm"})
        later = self._add_media("later.png")

        response = self._upload(backup)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True})
        layouts = self.client.get("/api/layout").json
        self.assertEqual([layout["name"] for layout in layouts], ["Kept"])
        medias = self.client.get("/api/media").json
        self.assertEqual([media["name"] for media in medias], ["kept.png"])
        # The files themselves, not just the rows: the one the backup held
        # is back on disk and the one added since is gone with its row.
        self.assertEqual(self._media_files(), [kept["filename"]])
        self.assertNotIn(later["filename"], self._media_files())
        self.assertEqual(
            self.client.get(f"/api/media/{kept['filename']}").get_data(),
            b"\x89PNG-ish",
        )

    def test_a_restore_of_a_backup_with_no_media_empties_the_library(self):
        backup = self._download()
        self._add_media()

        self._upload(backup)

        self.assertEqual(self.client.get("/api/media").json, [])
        self.assertEqual(self._media_files(), [])

    def test_the_app_keeps_serving_from_the_restored_database(self):
        # Not just the one read above: the connection every later request
        # opens has to land on the new file, so a write goes into it too.
        backup = self._download()
        self.client.post("/api/layout", json={"name": "Later", "unit": "mm"})
        self._upload(backup)

        created = self.client.post(
            "/api/layout", json={"name": "After restore", "unit": "mm"}
        )
        self.assertEqual(created.status_code, 201)
        layouts = self.client.get("/api/layout").json
        self.assertEqual([layout["name"] for layout in layouts], ["After restore"])

    def test_a_restore_migrates_a_backup_taken_by_an_older_kbrd(self):
        # Restoring has to run the same migrations a startup does, or the
        # app comes back up reading columns and tables that aren't there.
        archive = self._archive(
            {"database.db": self._database(self.LEGACY_SCHEMA)}
        )

        response = self._upload(archive)

        self.assertEqual(response.status_code, 200)
        layouts = self.client.get("/api/layout").json
        self.assertEqual([layout["name"] for layout in layouts], ["Old layout"])
        # Renamed, re-columned, and still carrying what it held.
        layers = self.client.get(f"/api/layout/{layouts[0]['id']}/layer").json
        self.assertEqual([layer["name"] for layer in layers], ["Old layer"])
        self.assertIsNone(layers[0]["factory_layout"])

    # -- what a restore refuses -----------------------------------------

    def test_a_restore_without_a_file_is_refused(self):
        response = self.client.post(
            "/api/backup/restore",
            data={},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_a_file_that_is_not_an_archive_is_refused(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})

        response = self._upload(b"\x89PNG-ish", filename="art.png")

        self.assertEqual(response.status_code, 400)
        self._assert_untouched()

    def test_an_archive_with_no_database_in_it_is_refused(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})

        response = self._upload(self._archive({"notes.txt": b"nothing here"}))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "not a KBRD backup")
        self._assert_untouched()

    def test_an_archive_holding_a_database_from_somewhere_else_is_refused(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})
        stranger = self._database("CREATE TABLE notes (id INTEGER PRIMARY KEY);")

        response = self._upload(self._archive({"database.db": stranger}))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "not a KBRD backup")
        self._assert_untouched()

    def test_an_archive_holding_something_that_is_not_a_database_is_refused(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})

        response = self._upload(self._archive({"database.db": b"\x89PNG-ish"}))

        self.assertEqual(response.status_code, 400)
        self._assert_untouched()

    def test_a_damaged_backup_is_refused(self):
        self.client.post("/api/layout", json={"name": "Kept", "unit": "mm"})
        with zipfile.ZipFile(io.BytesIO(self._download())) as archive:
            database = bytearray(archive.read("database.db"))
        # A whole page overwritten: the file still opens as a database and
        # only falls over once something actually reads it, which is what
        # the staged copy is opened for.
        self.assertGreater(len(database), 8192)
        database[4096:8192] = b"\xff" * 4096

        response = self._upload(self._archive({"database.db": bytes(database)}))

        self.assertEqual(response.status_code, 400)
        self._assert_untouched()

    def test_an_entry_pointing_outside_the_media_folder_is_not_unpacked(self):
        # Nothing a backup ever writes, so nothing a restore unpacks: the
        # database still restores, and the entry is simply dropped.
        target = Path(self.media_dir.name).parent / "escaped.txt"
        archive = self._archive(
            {
                "database.db": self._database(self.LEGACY_SCHEMA),
                "media/../../escaped.txt": b"nope",
                "media/nested/deep.png": b"nope",
            }
        )

        response = self._upload(archive)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(target.exists())
        self.assertEqual(self._media_files(), [])

    def test_a_refused_restore_leaves_nothing_staged_beside_the_database(self):
        self._upload(b"not an archive at all")

        self.assertFalse(os.path.exists(f"{self.db_path}.restoring"))
        self.assertFalse(os.path.exists(f"{self.media_dir.name}.restoring"))

    def _assert_untouched(self):
        """The device is as it was — a refused restore replaces nothing."""
        layouts = self.client.get("/api/layout").json
        self.assertEqual([layout["name"] for layout in layouts], ["Kept"])
