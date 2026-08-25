import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class GeometryApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        app, _ = create_app(Config(db_path=self.db_path))
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_create_and_get_active_layout(self):
        response = self.client.post("/api/geometry", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [{
                "elements": [
                    [{"size": 16, "rowspan": 2}],
                    [{"size": 16}],
                ],
            }],
        })
        self.assertEqual(response.status_code, 201)

        active = self.client.get("/api/geometry/active")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json["unit"], "mm")
        self.assertEqual(active.json["layout"]["keys"][1]["x"], 19)

    def test_rejects_invalid_payload(self):
        response = self.client.post("/api/geometry", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": "not an array",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "geometry must be an array")

    def test_activates_a_geometry(self):
        first = self.client.post("/api/geometry", json={
            "name": "Default",
            "unit": "px",
            "geometry": [],
        }).json
        second = self.client.post("/api/geometry", json={
            "name": "Alternative",
            "unit": "px",
            "geometry": [],
        }).json

        activated = self.client.put(f"/api/geometry/{second['id']}/activate")
        self.assertEqual(activated.status_code, 200)
        self.assertTrue(activated.json["active"])
        self.assertEqual(self.client.get("/api/geometry/active").json["id"], second["id"])
        self.assertNotEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
