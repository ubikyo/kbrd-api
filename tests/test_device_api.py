import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.api.device import Device
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    Device = None
    create_app = None


@unittest.skipIf(create_app is None, "Flask is not installed")
class DeviceApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        os.unlink(self.db_path)

    def _client(self, device):
        from flask import Flask

        proxy_app = Flask(__name__)
        device.register(proxy_app)
        proxy_app.testing = True
        return proxy_app.test_client()

    def test_create_app_registers_device_routes(self):
        app, _ = create_app(Config(db_path=self.db_path))
        response = app.test_client().get("/api/device")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"connected": False})

    def test_registers_and_reports_resolution(self):
        client = self._client(Device(clock=lambda: 100))
        registered = client.post(
            "/api/device/register",
            json={"width": 1280, "height": 400},
        )
        self.assertEqual(registered.status_code, 200)

        status = client.get("/api/device")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json,
            {
                "connected": True,
                "width": 1280,
                "height": 400,
                "width_mm": None,
                "height_mm": None,
            },
        )

    def test_registers_and_reports_the_physical_size_when_sent(self):
        client = self._client(Device(clock=lambda: 100))
        client.post(
            "/api/device/register",
            json={"width": 1280, "height": 400, "width_mm": 154, "height_mm": 85},
        )

        status = client.get("/api/device")
        self.assertEqual(
            status.json,
            {
                "connected": True,
                "width": 1280,
                "height": 400,
                "width_mm": 154,
                "height_mm": 85,
            },
        )

    def test_rejects_invalid_resolution(self):
        client = self._client(Device(clock=lambda: 100))
        response = client.post(
            "/api/device/register",
            json={"width": 0, "height": 400},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_physical_size(self):
        client = self._client(Device(clock=lambda: 100))
        response = client.post(
            "/api/device/register",
            json={"width": 1280, "height": 400, "width_mm": 0, "height_mm": 85},
        )
        self.assertEqual(response.status_code, 400)

    def test_expires_stale_registration(self):
        now = [100]
        client = self._client(Device(clock=lambda: now[0]))
        client.post(
            "/api/device/register",
            json={"width": 1280, "height": 400},
        )
        now[0] += Device.REGISTRATION_TTL_SECONDS + 1
        self.assertEqual(
            client.get("/api/device").json,
            {"connected": False},
        )
