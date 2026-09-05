import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class LayoutApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        app, _ = create_app(Config(db_path=self.db_path))
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_create_and_get_active_layout(self):
        response = self.client.post("/api/layout", json={
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

        active = self.client.get("/api/layout/active")
        self.assertEqual(active.status_code, 200)
        self.assertEqual(active.json["unit"], "mm")
        self.assertEqual(active.json["layout"]["keys"][1]["x"], 19)

    def test_defaults_and_persists_caps_and_gap_size(self):
        created = self.client.post("/api/layout", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
        }).json
        # Not sent at all — falls back to KBRD-DEV's reference panel.
        self.assertEqual(created["unit_mm"], 19.05)
        self.assertEqual(created["gap_mm"], 3)
        self.assertNotIn("physical_width_mm", created)

        updated = self.client.put(f"/api/layout/{created['id']}", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
            "unit_mm": 16,
            "gap_mm": 2.5,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["unit_mm"], 16)
        self.assertEqual(updated.json["gap_mm"], 2.5)

        # Survives a fresh fetch — not just echoed back from the request.
        fetched = self.client.get(f"/api/layout/{created['id']}")
        self.assertEqual(fetched.json["unit_mm"], 16)

    def test_max_columns_and_rows_default_to_null_and_persist_when_set(self):
        created = self.client.post("/api/layout", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
        }).json
        # Not sent at all — null means "as many as fit" to kbrd-web.
        self.assertIsNone(created["max_columns"])
        self.assertIsNone(created["max_rows"])

        updated = self.client.put(f"/api/layout/{created['id']}", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
            "max_columns": 6,
            "max_rows": 4,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["max_columns"], 6)
        self.assertEqual(updated.json["max_rows"], 4)

        fetched = self.client.get(f"/api/layout/{created['id']}")
        self.assertEqual(fetched.json["max_columns"], 6)
        self.assertEqual(fetched.json["max_rows"], 4)

        # Explicitly clearing it back to null works too.
        cleared = self.client.put(f"/api/layout/{created['id']}", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
            "max_columns": None,
            "max_rows": None,
        })
        self.assertIsNone(cleared.json["max_columns"])
        self.assertIsNone(cleared.json["max_rows"])

    def test_max_columns_and_rows_accept_quarter_steps(self):
        # kbrd-web's own NumberInput steps these by 0.25, like a cell's own
        # Unit — not by a whole 1U item at a time.
        created = self.client.post("/api/layout", json={
            "name": "Default",
            "unit": "mm",
            "geometry": [],
            "max_columns": 6.25,
            "max_rows": 4.5,
        }).json
        self.assertEqual(created["max_columns"], 6.25)
        self.assertEqual(created["max_rows"], 4.5)

        fetched = self.client.get(f"/api/layout/{created['id']}")
        self.assertEqual(fetched.json["max_columns"], 6.25)
        self.assertEqual(fetched.json["max_rows"], 4.5)

    def test_rejects_invalid_max_columns_and_rows(self):
        response = self.client.post("/api/layout", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": [],
            "max_columns": 0,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "max_columns must be at least 1")

        response = self.client.post("/api/layout", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": [],
            "max_rows": "not a number",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "max_rows must be a number or null")

    def test_rejects_invalid_physical_dimensions(self):
        response = self.client.post("/api/layout", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": [],
            "gap_mm": -1,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "gap_mm must not be negative")

        response = self.client.post("/api/layout", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": [],
            "unit_mm": "not a number",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "unit_mm must be a number")

    def test_rejects_invalid_payload(self):
        response = self.client.post("/api/layout", json={
            "name": "Invalid",
            "unit": "mm",
            "geometry": "not an array",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"], "geometry must be an array")

    def test_activates_a_layout(self):
        first = self.client.post("/api/layout", json={
            "name": "Default",
            "unit": "px",
            "geometry": [],
        }).json
        second = self.client.post("/api/layout", json={
            "name": "Alternative",
            "unit": "px",
            "geometry": [],
        }).json

        activated = self.client.put(f"/api/layout/{second['id']}/activate")
        self.assertEqual(activated.status_code, 200)
        self.assertTrue(activated.json["active"])
        self.assertEqual(self.client.get("/api/layout/active").json["id"], second["id"])
        self.assertNotEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
