import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.api.display import Display
    from kbrd_api.api.network import Network
    from kbrd_api.api.setup import Setup
    from kbrd_api.db import DB
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None


WAVESHARE = {
    "name": "Waveshare 10.1-DSI-TOUCH-A",
    "brand": "Waveshare",
    "model": "10.1-DSI-TOUCH-A",
    "physical_width_mm": 216,
    "physical_height_mm": 135,
}

NETWORK = {
    "ssid": "Unify",
    "passphrase": "hunter22",
    "ipv4": {"mode": "dhcp"},
}


@unittest.skipIf(create_app is None, "Flask is not installed")
class SetupApiTest(unittest.TestCase):
    """The wizard over a `Network` that reaches nothing.

    `/usr/bin/kbrd-network` only exists on a keyboard, and a completed
    wizard queues an apply through it — so the helper is stubbed out here
    the way `test_network_api.py` stubs it, and the timer that apply is
    armed on is pushed out of the way rather than left to fire at a
    machine running the tests. `self.applies` is what ran through the
    helper; a test that wants the queued apply fires it itself.
    """

    def setUp(self):
        from flask import Flask

        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.network_path = tempfile.mkstemp(suffix=".conf")
        os.close(handle)
        os.unlink(self.network_path)
        handle, self.reset_path = tempfile.mkstemp(suffix=".txt")
        os.close(handle)
        os.unlink(self.reset_path)

        self.applies = []
        db = DB(self.db_path)
        db.init_schema()
        self.network = Network(
            config_path=self.network_path,
            runner=self._run,
            apply_delay=3600,
        )
        network = self.network

        app = Flask(__name__)
        app.testing = True
        Setup(db, network, self.reset_path).register(app)
        Display(db).register(app)
        network.register(app)
        self.client = app.test_client()

    def _run(self, argv, timeout):
        self.applies.append(argv[-1])
        return ""

    def tearDown(self):
        os.unlink(self.db_path)
        for path in (self.network_path, self.reset_path):
            if os.path.exists(path):
                os.unlink(path)

    def test_create_app_registers_the_setup_route(self):
        app, _ = create_app(
            Config(
                db_path=self.db_path,
                network_config_path=self.network_path,
            )
        )
        response = app.test_client().get("/api/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("configured", response.json)

    # ------------------------------------------------------------------
    # The flag
    # ------------------------------------------------------------------

    def test_a_fresh_database_has_not_been_through_the_wizard(self):
        state = self.client.get("/api/setup").json
        self.assertFalse(state["configured"])
        # The reference panel is what the row starts on, but with no name
        # on it — nothing has declared a screen yet.
        self.assertEqual(state["display"]["physical_width_mm"], 216)
        self.assertEqual(state["display"]["name"], "")

    def test_completing_the_wizard_is_what_sets_the_flag(self):
        response = self.client.post(
            "/api/setup", json={"display": WAVESHARE, "network": NETWORK}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["configured"])
        self.assertTrue(response.json["network_applied"])

        state = self.client.get("/api/setup").json
        self.assertTrue(state["configured"])
        self.assertEqual(state["display"]["name"], "Waveshare 10.1-DSI-TOUCH-A")
        self.assertEqual(state["display"]["brand"], "Waveshare")
        self.assertEqual(state["display"]["physical_height_mm"], 135)

        # The network is queued, not joined inside the request: it takes
        # the hotspot down, and the hotspot is what the wizard came in on.
        self.assertEqual(self.applies, [])
        self.network.apply()
        self.assertEqual(self.applies, ["apply"])

    def test_a_screen_described_by_hand_carries_no_brand_or_model(self):
        self.client.post(
            "/api/setup",
            json={
                "display": {
                    "name": "Écran du prototype",
                    "physical_width_mm": 180.5,
                    "physical_height_mm": 110,
                }
            },
        )
        display = self.client.get("/api/setup").json["display"]
        self.assertEqual(display["name"], "Écran du prototype")
        self.assertEqual(display["brand"], "")
        self.assertEqual(display["model"], "")
        self.assertEqual(display["physical_width_mm"], 180.5)

    def test_the_screen_lands_on_the_display_route_too(self):
        self.client.post("/api/setup", json={"display": WAVESHARE})
        display = self.client.get("/api/display").json
        self.assertEqual(display["physical_width_mm"], 216)
        self.assertEqual(display["model"], "10.1-DSI-TOUCH-A")

    # ------------------------------------------------------------------
    # Back to the wizard
    # ------------------------------------------------------------------

    def test_a_marker_sends_a_configured_device_back_to_the_wizard(self):
        self.client.post("/api/setup", json={"display": WAVESHARE})
        self.assertTrue(self.client.get("/api/setup").json["configured"])

        self._ask_reset()
        state = self.client.get("/api/setup").json
        self.assertFalse(state["configured"])
        # The screen it was told about is still there — this asks for the
        # wizard, it doesn't wipe the device.
        self.assertEqual(state["display"]["name"], "Waveshare 10.1-DSI-TOUCH-A")

    def test_finishing_the_wizard_is_what_takes_the_marker_away(self):
        self.client.post("/api/setup", json={"display": WAVESHARE})
        self._ask_reset()

        self.client.post("/api/setup", json={"display": WAVESHARE})
        self.assertFalse(os.path.exists(self.reset_path))
        self.assertTrue(self.client.get("/api/setup").json["configured"])

    def test_a_wizard_abandoned_against_a_marker_asks_again(self):
        self.client.post("/api/setup", json={"display": WAVESHARE})
        self._ask_reset()

        # Refused half-way: nothing is written, and the marker is still
        # there to ask again with.
        response = self.client.post("/api/setup", json={"display": {}})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(os.path.exists(self.reset_path))
        self.assertFalse(self.client.get("/api/setup").json["configured"])

    def test_a_service_told_of_no_marker_has_no_way_back(self):
        from flask import Flask

        app = Flask(__name__)
        app.testing = True
        Setup(DB(self.db_path), self.network).register(app)
        client = app.test_client()

        client.post("/api/setup", json={"display": WAVESHARE})
        # Even with a file sitting exactly where the other service looks.
        self._ask_reset()
        self.assertTrue(client.get("/api/setup").json["configured"])

    def _ask_reset(self) -> None:
        with open(self.reset_path, "w") as marker:
            marker.write("")

    # ------------------------------------------------------------------
    # The password
    # ------------------------------------------------------------------

    def test_the_wizard_is_what_sets_the_password(self):
        from kbrd_api import password

        state = self.client.get("/api/setup").json
        self.assertFalse(state["password_set"])

        response = self.client.post(
            "/api/setup", json={"display": WAVESHARE, "password": "hunter22"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get("/api/setup").json["password_set"])

        # Stored as a digest and nothing else — what is in the column
        # answers to the password and is not the password.
        stored = self._password_hash()
        self.assertNotIn("hunter22", stored)
        self.assertTrue(password.verify("hunter22", stored))

    def test_the_password_never_comes_back_out(self):
        self.client.post(
            "/api/setup", json={"display": WAVESHARE, "password": "hunter22"}
        )
        body = self.client.get("/api/setup").get_data(as_text=True)
        self.assertNotIn("hunter22", body)
        self.assertNotIn(self._password_hash(), body)

    def test_a_wizard_that_asked_for_none_leaves_the_device_without_one(self):
        for name, body in {
            "left out": {"display": WAVESHARE},
            "null": {"display": WAVESHARE, "password": None},
        }.items():
            with self.subTest(name):
                self.client.post("/api/setup", json=body)
                self.assertEqual(self._password_hash(), "")
                self.assertFalse(
                    self.client.get("/api/setup").json["password_set"]
                )

    def test_a_password_this_service_refuses_writes_nothing_at_all(self):
        # The same all-or-nothing as the screen and the network below: a
        # password too short leaves the device unconfigured rather than
        # set up without one.
        response = self.client.post(
            "/api/setup", json={"display": WAVESHARE, "password": "short"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("8", response.json["error"])
        self.assertFalse(self.client.get("/api/setup").json["configured"])
        self.assertEqual(self._password_hash(), "")

    def _password_hash(self) -> str:
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT password_hash FROM credential WHERE id=1"
            ).fetchone()[0]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # All or nothing
    # ------------------------------------------------------------------

    def test_a_refused_call_leaves_the_device_unconfigured(self):
        cases = {
            "no display": {},
            "no name": {
                "display": {"physical_width_mm": 216, "physical_height_mm": 135}
            },
            "no size": {"display": {"name": "Panel"}},
            "a size that is not a number": {
                "display": {
                    "name": "Panel",
                    "physical_width_mm": "wide",
                    "physical_height_mm": 135,
                }
            },
            "a screen too small to be one": {
                "display": {
                    "name": "Panel",
                    "physical_width_mm": 2,
                    "physical_height_mm": 135,
                }
            },
            "a screen too large to be one": {
                "display": {
                    "name": "Panel",
                    "physical_width_mm": 216,
                    "physical_height_mm": 9000,
                }
            },
        }
        for name, body in cases.items():
            with self.subTest(name):
                response = self.client.post("/api/setup", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)

        self.assertFalse(self.client.get("/api/setup").json["configured"])

    def test_a_network_the_network_route_would_refuse_writes_nothing_at_all(self):
        # The whole point of one call rather than two: a screen that is
        # fine and a network that isn't leaves *neither* behind, so the
        # next load starts the wizard over rather than dropping the user
        # into a device that is part configured.
        response = self.client.post(
            "/api/setup",
            json={
                "display": WAVESHARE,
                "network": {"ssid": "Unify", "passphrase": "short"},
            },
        )
        self.assertEqual(response.status_code, 400)

        self.assertFalse(self.client.get("/api/setup").json["configured"])
        self.assertEqual(self.client.get("/api/display").json["name"], "")
        self.assertFalse(os.path.exists(self.network_path))

    # ------------------------------------------------------------------
    # Staying on the hotspot
    # ------------------------------------------------------------------

    def test_no_network_at_all_is_an_answer(self):
        response = self.client.post("/api/setup", json={"display": WAVESHARE})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["configured"])
        # Nothing to reconnect to: the keyboard stays on its hotspot, and
        # the wizard can hand straight over to the app.
        self.assertFalse(response.json["network_applied"])
        self.assertFalse(os.path.exists(self.network_path))

    def test_the_network_is_saved_for_the_next_boot_as_well(self):
        self.client.post(
            "/api/setup", json={"display": WAVESHARE, "network": NETWORK}
        )
        saved = self.client.get("/api/network").json["config"]
        self.assertEqual(saved["ssid"], "Unify")
        self.assertTrue(saved["secured"])
        self.assertEqual(saved["ipv4"]["mode"], "dhcp")


if __name__ == "__main__":
    unittest.main()
