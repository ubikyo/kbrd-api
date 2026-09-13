import os

from flask import Flask, jsonify, request

from kbrd_api import password as passwords
from kbrd_api.db import DB
from kbrd_api.api.network import Network, NetworkError


class Setup:
    """The first run, and the one flag that says it is over.

    A keyboard out of the box has no layout, no screen size worth the
    name and no network but its own hotspot, and KBRD-WEB shows a wizard
    instead of the app until it has been through all of that. What the
    wizard collects is written here, in one call, at the end: the screen
    it is attached to, the Wi-Fi to join, and the password KBRD-WEB is to
    ask for from then on (see `password.py` — the digest is what is
    stored, never the password).

    One call and not two on purpose — until every step has been answered,
    none of them counts. A wizard abandoned half-way leaves `configured`
    at 0 and nothing else changed, so the next load starts it over from
    the top rather than dropping the user into a device that is part
    configured.

    The network is applied last, and after the answer has gone out (see
    `Network.apply_later`): joining a network takes the hotspot down, and
    the browser running the wizard is on that hotspot. By then everything
    is saved, so what the user comes back to is the app.

    Going back to the wizard is a file: drop one at `reset_path` (see
    `Config`) and this answers `configured: false` for as long as it is
    there, whatever the database says. There is no route for it on
    purpose — the thing most likely to send someone back here is a
    password nobody remembers, and a route to undo that is a route to
    walk in through. A file on the data partition takes SSH, SFTP or the
    card itself, which is to say the device in your hands.

    Finishing the wizard is what removes it. An abandoned one leaves it
    where it is, and the next load asks again — the same all-or-nothing
    as the rest of this class.
    """

    # A screen this service will believe in, in millimetres: below the
    # first nothing could be a keyboard, above the second the number is a
    # slip of the finger rather than a panel.
    MIN_MM = 10
    MAX_MM = 2000

    NAME_MAX = 64

    def __init__(self, db: DB, network: Network, reset_path: str = ""):
        self.db = db
        self.network = network
        # Empty is a service with no way back to the wizard at all, which
        # is what a test that says nothing about it gets.
        self.reset_path = reset_path

    def register(self, app: Flask) -> None:
        @app.get("/api/setup")
        def setup_state():
            return jsonify(self.state())

        @app.post("/api/setup")
        def complete_setup():
            data = request.get_json(silent=True) or {}
            try:
                display = self._parse_display(data.get("display"))
                # Left out — or `null` — says this device has none, which
                # is what a database untouched by the wizard already
                # holds; anything else has to be one this service would
                # take (see `password.check`).
                secret = (
                    None
                    if data.get("password") is None
                    else passwords.check(data["password"])
                )
                # `null` — or nothing at all — is an answer: it says to
                # stay on the hotspot for now. Anything else has to be a
                # network this service would accept on its own endpoint.
                network = (
                    None
                    if data.get("network") is None
                    else self.network.parse(data["network"])
                )
            # `PasswordError` is a `ValueError`, so a password this
            # service won't take is refused with the rest of them.
            except (ValueError, NetworkError) as error:
                return jsonify(error=str(error)), 400

            self._write_display(display)
            # Before anything else that could fail: a wizard completed
            # against a marker still on disk would be answered by the
            # wizard again.
            self._clear_reset()
            if secret is not None:
                self._write_password(secret)
            if network is not None:
                self.network.save(network)
                self.network.apply_later()

            return jsonify(
                ok=True,
                configured=True,
                display=display,
                # Whether the caller is about to lose the connection it
                # asked over — the wizard says so rather than dropping
                # the user on an app that is about to stop answering.
                network_applied=network is not None,
            )

    def state(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT physical_width_mm, physical_height_mm,
                       name, brand, model, configured
                FROM display WHERE id=1
                """
            ).fetchone()
            credential = conn.execute(
                "SELECT password_hash FROM credential WHERE id=1"
            ).fetchone()
        return {
            # The file wins over the flag: a keyboard being sent back to
            # the wizard is being sent back to it.
            "configured": bool(row["configured"]) and not self._reset_asked(),
            # Whether there is one, and nothing about it: the digest
            # itself has no business leaving the device.
            "password_set": bool(credential and credential["password_hash"]),
            "display": {
                "physical_width_mm": row["physical_width_mm"],
                "physical_height_mm": row["physical_height_mm"],
                "name": row["name"],
                "brand": row["brand"],
                "model": row["model"],
            },
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _reset_asked(self) -> bool:
        return bool(self.reset_path) and os.path.exists(self.reset_path)

    def _clear_reset(self) -> None:
        """Take the marker away, if there is one and it can be taken.

        A file that won't go — a read-only mount, a permission — leaves
        the wizard asking, which is a device that can be set up over and
        over rather than one that can't be set up at all. That is the
        right way round, so nothing here is raised.
        """
        if not self.reset_path:
            return
        try:
            os.remove(self.reset_path)
        except OSError:
            pass

    def _write_password(self, secret: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE credential SET password_hash=? WHERE id=1",
                (passwords.hash_password(secret),),
            )
            conn.commit()

    def _write_display(self, display: dict) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE display
                SET physical_width_mm=?, physical_height_mm=?,
                    name=?, brand=?, model=?, configured=1
                WHERE id=1
                """,
                (
                    display["physical_width_mm"],
                    display["physical_height_mm"],
                    display["name"],
                    display["brand"],
                    display["model"],
                ),
            )
            conn.commit()

    @classmethod
    def _parse_display(cls, data) -> dict:
        if not isinstance(data, dict):
            raise ValueError("a screen is required")

        name = cls._text(data.get("name"), "name")
        if not name:
            raise ValueError("the screen needs a name")

        return {
            "name": name,
            # Only filled when the screen was picked out of KBRD-WEB's
            # own list of known panels; one described by hand has a name
            # and nothing else.
            "brand": cls._text(data.get("brand", ""), "brand"),
            "model": cls._text(data.get("model", ""), "model"),
            "physical_width_mm": cls._size(data, "physical_width_mm", "width"),
            "physical_height_mm": cls._size(data, "physical_height_mm", "height"),
        }

    @classmethod
    def _text(cls, value, label: str) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"invalid {label}")
        value = value.strip()
        if len(value) > cls.NAME_MAX:
            raise ValueError(f"the {label} must be at most {cls.NAME_MAX} characters")
        return value

    @classmethod
    def _size(cls, data: dict, key: str, label: str) -> float:
        try:
            number = float(data[key])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"the screen {label} must be a number") from None
        if not cls.MIN_MM <= number <= cls.MAX_MM:
            raise ValueError(
                f"the screen {label} must be between "
                f"{cls.MIN_MM} and {cls.MAX_MM} mm"
            )
        return number
