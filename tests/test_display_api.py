import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


def panel(width, height, name="", brand="", model=""):
    """A whole `/api/display` row.

    The route answers with the screen's identity as well as its size —
    written by the setup wizard (see `api/setup.py`), never by this route
    — so the tests below say what they are about (the size) without
    having to spell the rest out each time.
    """
    return {
        "physical_width_mm": width,
        "physical_height_mm": height,
        "name": name,
        "brand": brand,
        "model": model,
    }


@unittest.skipIf(create_app is None, "Flask is not installed")
class DisplayApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        app, _ = create_app(Config(db_path=self.db_path))
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_defaults_to_kbrd_devs_reference_panel(self):
        response = self.client.get("/api/display")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, panel(216, 135))

    def test_updates_and_persists_the_single_row(self):
        updated = self.client.put("/api/display", json={
            "physical_width_mm": 220,
            "physical_height_mm": 140,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json, panel(220, 140))

        fetched = self.client.get("/api/display")
        self.assertEqual(fetched.json, panel(220, 140))

    def test_is_shared_across_layouts_not_per_layout(self):
        first = self.client.post("/api/layout", json={
            "name": "First",
            "unit": "mm",
        }).json
        second = self.client.post("/api/layout", json={
            "name": "Second",
            "unit": "mm",
        }).json
        self.assertNotIn("physical_width_mm", first)
        self.assertNotIn("physical_width_mm", second)

        self.client.put("/api/display", json={
            "physical_width_mm": 300,
            "physical_height_mm": 150,
        })
        # Switching the active layout doesn't touch the display's own row.
        self.client.put(f"/api/layout/{second['id']}/activate")
        self.assertEqual(self.client.get("/api/display").json, panel(300, 150))

    def test_reports_the_screen_the_wizard_declared_and_leaves_it_alone(self):
        self.client.post("/api/setup", json={
            "display": {
                "name": "Waveshare 10.1-DSI-TOUCH-A",
                "brand": "Waveshare",
                "model": "10.1-DSI-TOUCH-A",
                "physical_width_mm": 216,
                "physical_height_mm": 135,
            },
        })
        self.assertEqual(
            self.client.get("/api/display").json,
            panel(216, 135, "Waveshare 10.1-DSI-TOUCH-A", "Waveshare", "10.1-DSI-TOUCH-A"),
        )

        # A size changed by hand in Settings says nothing about which
        # panel was declared, so it doesn't rewrite it.
        self.client.put("/api/display", json={
            "physical_width_mm": 220,
            "physical_height_mm": 140,
        })
        self.assertEqual(
            self.client.get("/api/display").json,
            panel(220, 140, "Waveshare 10.1-DSI-TOUCH-A", "Waveshare", "10.1-DSI-TOUCH-A"),
        )

    def test_rejects_invalid_dimensions(self):
        response = self.client.put("/api/display", json={
            "physical_width_mm": 0,
            "physical_height_mm": 135,
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json["error"], "physical_width_mm must be greater than zero"
        )

        response = self.client.put("/api/display", json={
            "physical_width_mm": "not a number",
            "physical_height_mm": 135,
        })
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
