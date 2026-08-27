import io
import os
import tempfile
import unittest
from urllib.error import URLError

from kbrd_api.config import Config

try:
    from kbrd_api.api.agent import Agent
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    Agent = None
    create_app = None


class FakeResponse:
    def __init__(self, payload=b"[]", status=200):
        self._payload = io.BytesIO(payload)
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self._payload.read()


@unittest.skipIf(create_app is None, "Flask is not installed")
class AgentApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        os.unlink(self.db_path)

    def _client(self, agent):
        from flask import Flask

        proxy_app = Flask(__name__)
        agent.register(proxy_app)
        proxy_app.testing = True
        return proxy_app.test_client()

    def test_create_app_registers_agent_routes(self):
        app, _ = create_app(Config(db_path=self.db_path))
        response = app.test_client().get("/api/agent")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"connected": False})

    def test_requires_a_live_agent(self):
        client = self._client(Agent(clock=lambda: 100))
        response = client.get("/api/applications")
        self.assertEqual(response.status_code, 503)

    def test_registers_and_forwards_application_requests(self):
        requests = []

        def open_request(outbound, timeout):
            requests.append((outbound, timeout))
            if outbound.full_url.endswith("/v1/applications"):
                return FakeResponse(b'[{"id":"com.example.App","name":"Example"}]')
            return FakeResponse(b'{"ok":true}')

        client = self._client(Agent(clock=lambda: 100, opener=open_request))
        registered = client.post(
            "/api/agent/register",
            json={
                "name": "Mac",
                "platform": "macos",
                "port": 8090,
                "token": "secret",
                "version": "1.0.0",
            },
            environ_base={"REMOTE_ADDR": "192.0.2.10"},
        )
        self.assertEqual(registered.status_code, 200)

        listed = client.get("/api/applications")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json[0]["id"], "com.example.App")
        launched = client.post(
            "/api/applications/com.example.App/launch"
        )
        self.assertEqual(launched.status_code, 200)
        self.assertEqual(
            requests[-1][0].full_url,
            "http://192.0.2.10:8090/v1/applications/com.example.App/launch",
        )
        self.assertEqual(
            requests[-1][0].get_header("Authorization"), "Bearer secret"
        )

    def test_reports_an_unreachable_agent(self):
        def unavailable(*args, **kwargs):
            raise URLError("offline")

        client = self._client(Agent(clock=lambda: 100, opener=unavailable))
        client.post(
            "/api/agent/register",
            json={"name": "Mac", "port": 8090, "token": "secret"},
        )
        response = client.get("/api/applications")
        self.assertEqual(response.status_code, 502)

    def test_expires_stale_registration(self):
        now = [100]
        client = self._client(Agent(clock=lambda: now[0]))
        client.post(
            "/api/agent/register",
            json={"name": "Mac", "port": 8090, "token": "secret"},
        )
        now[0] += Agent.REGISTRATION_TTL_SECONDS + 1
        self.assertEqual(client.get("/api/applications").status_code, 503)
