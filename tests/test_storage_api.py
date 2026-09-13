import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.api.storage import Storage
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    Storage = None
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class StorageApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        os.unlink(self.db_path)

    def _client(self, storage):
        from flask import Flask

        proxy_app = Flask(__name__)
        storage.register(proxy_app)
        proxy_app.testing = True
        return proxy_app.test_client()

    def test_create_app_registers_the_storage_route(self):
        app, _ = create_app(Config(db_path=self.db_path))
        response = app.test_client().get("/api/storage")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json["partitions"], list)

    def test_reports_a_real_reading_for_a_mount_point_that_exists(self):
        with tempfile.TemporaryDirectory() as path:
            client = self._client(Storage(partitions=(("Temp", path),)))
            partitions = client.get("/api/storage").json["partitions"]

        self.assertEqual(len(partitions), 1)
        partition = partitions[0]
        self.assertEqual(partition["name"], "Temp")
        self.assertEqual(partition["mount"], path)
        self.assertGreater(partition["total"], 0)
        self.assertGreaterEqual(partition["used"], 0)
        self.assertLessEqual(partition["used"] + partition["free"], partition["total"])

    def test_leaves_out_a_mount_point_that_is_not_there(self):
        client = self._client(Storage(partitions=(("Missing", "/no/such/mount"),)))
        self.assertEqual(client.get("/api/storage").json["partitions"], [])

    def test_reports_one_filesystem_once(self):
        # `/boot` and `/data` are rarely filesystems of their own off the
        # device; whatever answers for them there answers for `/` here, and
        # saying so three times would be three copies of one reading.
        with tempfile.TemporaryDirectory() as path:
            nested = os.path.join(path, "nested")
            os.mkdir(nested)
            client = self._client(
                Storage(partitions=(("First", path), ("Second", nested)))
            )
            partitions = client.get("/api/storage").json["partitions"]

        self.assertEqual([item["name"] for item in partitions], ["First"])


if __name__ == "__main__":
    unittest.main()
