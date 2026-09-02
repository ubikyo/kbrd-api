import threading
import time
from dataclasses import dataclass

from flask import Flask, jsonify, request


@dataclass(frozen=True)
class RegisteredDevice:
    width: int
    height: int
    seen_at: float
    width_mm: int | None = None
    height_mm: int | None = None


class Device:
    """Tracks the KBRD-DEV unit currently reporting its screen resolution.

    KBRD-DEV pushes its own registration on startup and then re-registers on
    an interval (see `kbrd_dev.device_registration`), the same push model
    already used by KBRD-Agent (`api/agent.py`). A registration older than
    `REGISTRATION_TTL_SECONDS` is treated as stale so KBRD-WEB can tell a
    silent device apart from one that never connected.
    """

    REGISTRATION_TTL_SECONDS = 30

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._device = None

    def register(self, app: Flask) -> None:
        @app.post("/api/device/register")
        def register_device():
            data = request.get_json(silent=True) or {}
            try:
                width = int(data.get("width"))
                height = int(data.get("height"))
            except (TypeError, ValueError):
                return jsonify(error="invalid device registration"), 400
            if width <= 0 or height <= 0:
                return jsonify(error="invalid device registration"), 400

            # The physical size isn't always known (some panels don't report
            # it in their EDID — see `kbrd_dev.edid`), so it's optional, but
            # whatever KBRD-DEV does send for it must be a valid size.
            width_mm = height_mm = None
            if "width_mm" in data or "height_mm" in data:
                try:
                    width_mm = int(data.get("width_mm"))
                    height_mm = int(data.get("height_mm"))
                except (TypeError, ValueError):
                    return jsonify(error="invalid device registration"), 400
                if width_mm <= 0 or height_mm <= 0:
                    return jsonify(error="invalid device registration"), 400

            device = RegisteredDevice(
                width=width,
                height=height,
                seen_at=self._clock(),
                width_mm=width_mm,
                height_mm=height_mm,
            )
            with self._lock:
                self._device = device
            return jsonify(
                ok=True,
                width=device.width,
                height=device.height,
                width_mm=device.width_mm,
                height_mm=device.height_mm,
            )

        @app.get("/api/device")
        def device_status():
            device = self._current()
            if device is None:
                return jsonify(connected=False)
            return jsonify(
                connected=True,
                width=device.width,
                height=device.height,
                width_mm=device.width_mm,
                height_mm=device.height_mm,
            )

    def _current(self):
        with self._lock:
            device = self._device
        if (
            device is None
            or self._clock() - device.seen_at > self.REGISTRATION_TTL_SECONDS
        ):
            return None
        return device
