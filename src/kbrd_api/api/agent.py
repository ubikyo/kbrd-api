import json
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, request


@dataclass(frozen=True)
class RegisteredAgent:
    host: str
    port: int
    token: str
    name: str
    platform: str
    version: str
    seen_at: float


class Agent:
    REGISTRATION_TTL_SECONDS = 30

    def __init__(self, clock=time.monotonic, opener=urlopen):
        self._clock = clock
        self._opener = opener
        self._lock = threading.Lock()
        self._agent = None

    def register(self, app: Flask) -> None:
        @app.post("/api/agent/register")
        def register_agent():
            data = request.get_json(silent=True) or {}
            host = request.remote_addr or ""
            try:
                port = int(data.get("port"))
            except (TypeError, ValueError):
                port = 0
            token = str(data.get("token") or "").strip()
            name = str(data.get("name") or "").strip()
            platform = str(data.get("platform") or "").strip()
            version = str(data.get("version") or "").strip()
            if not host or not 1 <= port <= 65535 or not token or not name:
                return jsonify(error="invalid agent registration"), 400
            agent = RegisteredAgent(
                host=host,
                port=port,
                token=token,
                name=name,
                platform=platform,
                version=version,
                seen_at=self._clock(),
            )
            with self._lock:
                self._agent = agent
            return jsonify(
                ok=True,
                name=agent.name,
                platform=agent.platform,
                version=agent.version,
            )

        @app.get("/api/agent")
        def agent_status():
            agent = self._current()
            if agent is None:
                return jsonify(connected=False)
            return jsonify(
                connected=True,
                name=agent.name,
                platform=agent.platform,
                version=agent.version,
            )

        @app.get("/api/applications")
        def list_applications():
            return self._proxy("GET", "/v1/applications")

        @app.post("/api/applications/<path:application_id>/launch")
        def launch_application(application_id):
            return self._application_action(application_id, "launch")

        @app.post("/api/applications/<path:application_id>/quit")
        def quit_application(application_id):
            return self._application_action(application_id, "quit")

        @app.get("/api/browsers")
        def list_browsers():
            return self._proxy("GET", "/v1/browsers")

        @app.post("/api/browsers/<path:browser_id>/open")
        def open_browser(browser_id):
            browser_id = browser_id.strip()
            if not browser_id:
                return jsonify(error="invalid browser id"), 400
            url = str((request.get_json(silent=True) or {}).get("url") or "").strip()
            if not url:
                return jsonify(error="missing url"), 400
            encoded_id = quote(browser_id, safe="")
            body = json.dumps({"url": url}).encode()
            return self._proxy(
                "POST", f"/v1/browsers/{encoded_id}/open", body=body
            )

    def _application_action(self, application_id: str, action: str):
        application_id = application_id.strip()
        if not application_id:
            return jsonify(error="invalid application id"), 400
        encoded_id = quote(application_id, safe="")
        return self._proxy("POST", f"/v1/applications/{encoded_id}/{action}")

    def _current(self):
        with self._lock:
            agent = self._agent
        if (
            agent is None
            or self._clock() - agent.seen_at > self.REGISTRATION_TTL_SECONDS
        ):
            return None
        return agent

    def _proxy(self, method: str, path: str, body: bytes | None = None):
        agent = self._current()
        if agent is None:
            return jsonify(error="KBRD Agent is unavailable"), 503
        host = f"[{agent.host}]" if ":" in agent.host else agent.host
        target = f"http://{host}:{agent.port}{path}"
        headers = {"Authorization": f"Bearer {agent.token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        forwarded = Request(target, data=body, method=method, headers=headers)
        try:
            with self._opener(forwarded, timeout=15) as response:
                payload = response.read()
                content_type = response.headers.get(
                    "Content-Type", "application/json"
                )
                return Response(
                    payload,
                    status=response.status,
                    content_type=content_type,
                )
        except HTTPError as error:
            payload = error.read()
            return Response(
                payload or json.dumps({"error": str(error)}),
                status=error.code,
                content_type=error.headers.get(
                    "Content-Type", "application/json"
                ),
            )
        except (OSError, URLError, TimeoutError):
            return jsonify(error="KBRD Agent cannot be reached"), 502
