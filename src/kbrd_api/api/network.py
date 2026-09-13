import os
import re
import shlex
import subprocess
import threading

from flask import Flask, jsonify, request


class NetworkError(Exception):
    """A request this service refuses, with the message to answer it with."""


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or len(part) > 3:
            return False
        if int(part) > 255:
            return False
    return True


def _is_netmask(value: str) -> bool:
    """A netmask is an IPv4 address whose set bits are contiguous.

    `255.255.255.0` is one, `255.0.255.0` is an address that only looks
    like one — and the second would be accepted by iwd and then produce a
    keyboard nobody can reach.
    """
    if not _is_ipv4(value):
        return False
    bits = 0
    for part in value.split("."):
        bits = (bits << 8) | int(part)
    # A contiguous mask, negated, is one less than a power of two.
    inverted = ~bits & 0xFFFFFFFF
    return inverted & (inverted + 1) == 0


class Network:
    """The keyboard's own Wi-Fi: what it is on now, and what to join next.

    Nothing here talks to iwd. Every privileged step — writing iwd's
    provisioning files, switching the interface between station and access
    point, scanning — is one shell script on the device,
    `/usr/bin/kbrd-network` (see KBRD-OS's `rootfs-overlay`), which this
    service runs under sudo. That script is also what runs at boot, so a
    setting saved here and a setting applied at power-on go through
    exactly the same code.

    What the user asks for is written to `/data/network/network.conf`, on
    the partition that survives a `make flash`, in the same `KEY=value`
    format the script reads its factory defaults in. Every value is
    validated before it gets there: the file is read by a shell, and these
    come off an HTTP request.

    Applying is deliberately *not* part of the response. Joining a network
    takes the interface down, and the browser asking for it is usually on
    the very interface in question — so the save is answered first and the
    apply runs a moment later, off the request thread, giving the answer
    time to leave.
    """

    CONFIG_PATH = "/data/network/network.conf"
    COMMAND = ("sudo", "/usr/bin/kbrd-network")

    # `status` reads a handful of files; `scan` waits on iwd's own scan
    # (the script sleeps 4s before collecting the results), so it gets a
    # ceiling of its own rather than the short one.
    STATUS_TIMEOUT_SECONDS = 15
    SCAN_TIMEOUT_SECONDS = 45
    # `apply` waits for an address before falling back to the hotspot —
    # CONNECT_TIMEOUT in the script's own configuration, 30s by default —
    # on top of a scan. The ceiling is there to stop a wedged helper
    # holding a thread forever, not to bound the normal case.
    APPLY_TIMEOUT_SECONDS = 180

    # How long the answer to a save has to reach the browser before the
    # interface is taken down under it.
    APPLY_DELAY_SECONDS = 1.0

    # WPA2's own range. An empty passphrase is something else entirely —
    # it means an open network, and is allowed.
    PASSPHRASE_MIN = 8
    PASSPHRASE_MAX = 63

    # What the script's `status` calls the two modes, and what this API
    # calls them: "station"/"ap" are iwd's words, not the interface's.
    MODES = {"station": "wifi", "ap": "hotspot"}

    # A value written into a file a shell reads back line by line: no
    # newline, and nothing unprintable.
    _FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")

    def __init__(
        self,
        config_path: str | None = None,
        command=None,
        runner=None,
        apply_delay: float | None = None,
    ):
        self._config_path = config_path or self.CONFIG_PATH
        self._command = tuple(command or self.COMMAND)
        self._runner = runner or self._run
        self._apply_delay = (
            self.APPLY_DELAY_SECONDS if apply_delay is None else apply_delay
        )
        self._lock = threading.Lock()

    def register(self, app: Flask) -> None:
        @app.get("/api/network")
        def network_status():
            return jsonify(self.status())

        @app.put("/api/network")
        def set_network():
            data = request.get_json(silent=True) or {}
            try:
                settings = self.parse(data)
            except NetworkError as error:
                return jsonify(error=str(error)), 400
            self.save(settings)
            self.apply_later()
            return jsonify(ok=True, config=self._public_config(settings))

        @app.post("/api/network/scan")
        def scan_networks():
            return jsonify(networks=self.scan())

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """What the interface is doing, and what it was asked to do.

        `available` is false wherever `/usr/bin/kbrd-network` isn't — a
        development machine, where KBRD-API runs off the keyboard — so
        KBRD-WEB can say so rather than show an empty network.
        """
        settings = self.load()
        config = self._public_config(settings)

        reading = self._status_lines()
        if reading is None:
            return {"available": False, "config": config}

        mode = self.MODES.get(reading.get("mode", ""), "unknown")
        ssid = reading.get("ssid", "")
        address = reading.get("address", "")
        dns = [value for value in reading.get("dns", "").split(",") if value]

        return {
            "available": True,
            "interface": reading.get("interface", ""),
            "mode": mode,
            # A hotspot is up the moment it is broadcasting; a network is
            # only joined once there is an address on the interface.
            "connected": bool(address) and (mode == "hotspot" or bool(ssid)),
            "ssid": ssid,
            "ipv4": {
                "address": address,
                "netmask": reading.get("netmask", ""),
                "gateway": reading.get("gateway", ""),
                "dns": dns,
            },
            "hotspot": {
                "ssid": reading.get("hotspot_ssid", ""),
                "address": reading.get("hotspot_address", ""),
            },
            "config": config,
        }

    def scan(self) -> list[dict]:
        """The networks in range, strongest first — iwd's own ordering.

        Works in hotspot mode too, which is the only reason it is here:
        the network to join is picked from a browser attached to the
        hotspot, over the very radio that has to do the scanning.
        """
        result = self._call("scan", timeout=self.SCAN_TIMEOUT_SECONDS)
        if result is None:
            return []

        networks = []
        seen = set()
        for line in result.splitlines():
            ssid, _, security = line.partition("\t")
            ssid = ssid.strip()
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append({"ssid": ssid, "security": security.strip()})
        return networks

    def load(self) -> dict:
        """The saved configuration, as it sits on the data partition."""
        values = {}
        try:
            with open(self._config_path, encoding="utf-8") as handle:
                for line in handle:
                    key, separator, value = line.partition("=")
                    if separator:
                        values[key.strip()] = value.strip()
        except OSError:
            return {}
        return values

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def parse(self, data: dict) -> dict:
        """Turn a request body into the `KEY=value` pairs the script reads.

        Raises `NetworkError` with the message to answer with rather than
        returning a partial configuration: half a static address is worse
        than none, since it is applied at the next boot too.
        """
        ssid = self._text(data.get("ssid"), "SSID")
        if not 1 <= len(ssid) <= 32:
            raise NetworkError("the SSID must be between 1 and 32 characters")

        # No `passphrase` at all means "the one already saved": the key
        # is never sent back out, so KBRD-WEB can't put it in a field for
        # the user to leave alone, and it would have to be retyped to
        # change anything else. An explicitly empty one is a different
        # answer — it says the network is open.
        if "passphrase" in data:
            passphrase = self._text(data.get("passphrase"), "passphrase")
        else:
            passphrase = self.load().get("PASSPHRASE", "")

        if passphrase and not (
            self.PASSPHRASE_MIN <= len(passphrase) <= self.PASSPHRASE_MAX
        ):
            raise NetworkError(
                "the key must be between "
                f"{self.PASSPHRASE_MIN} and {self.PASSPHRASE_MAX} characters"
            )

        ipv4 = data.get("ipv4") or {}
        if not isinstance(ipv4, dict):
            raise NetworkError("invalid IPv4 settings")

        mode = ipv4.get("mode", "dhcp")
        if mode not in ("dhcp", "static"):
            raise NetworkError("the IPv4 mode must be dhcp or static")

        settings = {
            "SSID": ssid,
            "PASSPHRASE": passphrase,
            "IPV4_MODE": mode,
            "IPV4_ADDRESS": "",
            "IPV4_NETMASK": "",
            "IPV4_GATEWAY": "",
            "IPV4_DNS1": "",
            "IPV4_DNS2": "",
        }
        if mode == "dhcp":
            return settings

        # An address without a mask isn't a static configuration, so both
        # are required. A gateway and DNS servers are not: a keyboard on a
        # network with no route out is a keyboard that still answers.
        for key, label in (("address", "address"), ("netmask", "netmask")):
            value = self._text(ipv4.get(key, ""), label)
            if not value:
                raise NetworkError(f"a static configuration needs an {label}")
            settings[f"IPV4_{key.upper()}"] = value

        if not _is_ipv4(settings["IPV4_ADDRESS"]):
            raise NetworkError("invalid IPv4 address")
        if not _is_netmask(settings["IPV4_NETMASK"]):
            raise NetworkError("invalid netmask")

        for key in ("gateway", "dns1", "dns2"):
            value = self._text(ipv4.get(key, ""), key)
            if value and not _is_ipv4(value):
                raise NetworkError(f"invalid {key} address")
            settings[f"IPV4_{key.upper()}"] = value

        return settings

    def save(self, settings: dict) -> None:
        lines = [
            "# Réseau enregistré depuis KBRD-WEB.",
            "#",
            "# Écrit par KBRD-API (voir api/network.py) et relu par",
            "# /usr/bin/kbrd-network, au démarrage comme à l'enregistrement.",
            "# Il l'emporte, clé par clé, sur /etc/kbrd/network.conf.",
            "",
        ]
        lines += [f"{key}={value}" for key, value in settings.items()]

        directory = os.path.dirname(self._config_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with self._lock:
            with open(self._config_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")

    def apply_later(self) -> None:
        """Join the saved network, once this request has been answered."""
        timer = threading.Timer(self._apply_delay, self.apply)
        timer.daemon = True
        timer.start()

    def apply(self) -> None:
        self._call("apply", timeout=self.APPLY_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _public_config(self, settings: dict) -> dict:
        """What KBRD-WEB is told about the saved configuration.

        The key never comes back out — `secured` says whether there is
        one, which is all the interface needs to show the field as
        already filled.
        """
        return {
            "ssid": settings.get("SSID", ""),
            "secured": bool(settings.get("PASSPHRASE", "")),
            "ipv4": {
                "mode": settings.get("IPV4_MODE") or "dhcp",
                "address": settings.get("IPV4_ADDRESS", ""),
                "netmask": settings.get("IPV4_NETMASK", ""),
                "gateway": settings.get("IPV4_GATEWAY", ""),
                "dns1": settings.get("IPV4_DNS1", ""),
                "dns2": settings.get("IPV4_DNS2", ""),
            },
        }

    def _status_lines(self) -> dict | None:
        result = self._call("status", timeout=self.STATUS_TIMEOUT_SECONDS)
        if result is None:
            return None
        reading = {}
        for line in result.splitlines():
            key, separator, value = line.partition("\t")
            if separator:
                reading[key] = value
        return reading

    def _text(self, value, label: str) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise NetworkError(f"invalid {label}")
        if self._FORBIDDEN.search(value):
            raise NetworkError(f"invalid {label}")
        return value.strip()

    def _call(self, *args, timeout) -> str | None:
        """Run the helper, or `None` if it isn't there or it failed."""
        try:
            return self._runner((*self._command, *args), timeout)
        except NetworkError:
            return None

    @staticmethod
    def _run(argv, timeout) -> str:
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise NetworkError(f"{shlex.join(argv)}: {error}") from error
        if completed.returncode != 0:
            raise NetworkError(
                f"{shlex.join(argv)} exited {completed.returncode}"
            )
        return completed.stdout
