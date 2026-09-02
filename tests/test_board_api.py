import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class BoardApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        app, _ = create_app(Config(db_path=self.db_path))
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_defaults_to_kbrd_devs_reference_panel(self):
        response = self.client.get("/api/board")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {
            "physical_width_mm": 216,
            "physical_height_mm": 135,
        })

    def test_updates_and_persists_the_single_row(self):
        updated = self.client.put("/api/board", json={
            "physical_width_mm": 220,
            "physical_height_mm": 140,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json, {
            "physical_width_mm": 220,
            "physical_height_mm": 140,
        })

        fetched = self.client.get("/api/board")
        self.assertEqual(fetched.json, {
            "physical_width_mm": 220,
            "physical_height_mm": 140,
        })

    def test_is_shared_across_geometries_not_per_layout(self):
        first = self.client.post("/api/geometry", json={
            "name": "First",
            "unit": "mm",
            "geometry": [],
        }).json
        second = self.client.post("/api/geometry", json={
            "name": "Second",
            "unit": "mm",
            "geometry": [],
        }).json
        self.assertNotIn("physical_width_mm", first)
        self.assertNotIn("physical_width_mm", second)

        self.client.put("/api/board", json={
            "physical_width_mm": 300,
            "physical_height_mm": 150,
        })
        # Switching the active geometry doesn't touch the board's own row.
        self.client.put(f"/api/geometry/{second['id']}/activate")
        self.assertEqual(self.client.get("/api/board").json, {
            "physical_width_mm": 300,
            "physical_height_mm": 150,
        })

    def test_rejects_invalid_dimensions(self):
        response = self.client.put("/api/board", json={
            "physical_width_mm": 0,
            "physical_height_mm": 135,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json["error"], "physical_width_mm must be greater than zero"
        )

        response = self.client.put("/api/board", json={
            "physical_width_mm": "not a number",
            "physical_height_mm": 135,
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
