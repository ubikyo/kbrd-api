import os
import tempfile
import unittest

from kbrd_api.config import Config

try:
    from kbrd_api.api.network import Network, NetworkError
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    Network = None
    NetworkError = None
    create_app = None


STATUS = "\n".join(
    [
        "interface\twlan0",
        "mode\tstation",
        "ssid\tUnify",
        "address\t192.168.1.50",
        "netmask\t255.255.255.0",
        "gateway\t192.168.1.1",
        "dns\t192.168.1.1,1.1.1.1",
        "hotspot_ssid\tKBRD-9f3c",
        "hotspot_address\t192.168.100.1",
    ]
)

HOTSPOT_STATUS = "\n".join(
    [
        "interface\twlan0",
        "mode\tap",
        "ssid\tKBRD-9f3c",
        "address\t192.168.100.1",
        "netmask\t255.255.255.0",
        "gateway\t",
        "dns\t",
        "hotspot_ssid\tKBRD-9f3c",
        "hotspot_address\t192.168.100.1",
    ]
)


class FakeHelper:
    """Stands in for `/usr/bin/kbrd-network`, which only exists on a device.

    Records every call so the tests can say *what* was asked of it, and
    answers from `outputs` keyed by the subcommand. A subcommand with no
    entry raises, which is how a helper that isn't there behaves.
    """

    def __init__(self, **outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        command = argv[-1]
        if command not in self.outputs:
            raise NetworkError(f"no such command: {command}")
        return self.outputs[command]


@unittest.skipIf(create_app is None, "Flask is not installed")
class NetworkApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.config_path = tempfile.mkstemp(suffix=".conf")
        os.close(handle)
        # Written by the tests that mean to, not by this one: `load`
        # answers `{}` for a file that isn't there, which is the state a
        # keyboard out of the box is in.
        os.unlink(self.config_path)

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.unlink(self.config_path)

    def _network(self, helper=None, **kwargs):
        return Network(
            config_path=self.config_path,
            runner=helper or FakeHelper(),
            # Long enough that the timer a save arms never fires
            # during a test: the tests that want the apply call it
            # themselves, and the one that doesn't checks it hasn't run.
            # The timer's thread is a daemon, so a pending one doesn't
            # hold the process open.
            apply_delay=3600,
            **kwargs,
        )

    def _client(self, network):
        from flask import Flask

        app = Flask(__name__)
        network.register(app)
        app.testing = True
        return app.test_client()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def test_create_app_registers_the_network_routes(self):
        app, _ = create_app(
            Config(db_path=":memory:", network_config_path=self.config_path)
        )
        client = app.test_client()
        self.assertEqual(client.get("/api/network").status_code, 200)
        self.assertEqual(client.post("/api/network/scan").status_code, 200)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def test_reports_the_joined_network(self):
        client = self._client(self._network(FakeHelper(status=STATUS)))
        status = client.get("/api/network").json

        self.assertTrue(status["available"])
        self.assertEqual(status["mode"], "wifi")
        self.assertTrue(status["connected"])
        self.assertEqual(status["ssid"], "Unify")
        self.assertEqual(status["ipv4"]["address"], "192.168.1.50")
        self.assertEqual(status["ipv4"]["dns"], ["192.168.1.1", "1.1.1.1"])
        self.assertEqual(status["hotspot"]["address"], "192.168.100.1")

    def test_reports_the_hotspot(self):
        client = self._client(self._network(FakeHelper(status=HOTSPOT_STATUS)))
        status = client.get("/api/network").json

        self.assertEqual(status["mode"], "hotspot")
        self.assertTrue(status["connected"])
        self.assertEqual(status["ipv4"]["address"], "192.168.100.1")
        # Neither is announced on the hotspot: the keyboard routes nothing.
        self.assertEqual(status["ipv4"]["gateway"], "")
        self.assertEqual(status["ipv4"]["dns"], [])

    def test_an_interface_with_no_address_is_not_connected(self):
        reading = STATUS.replace("address\t192.168.1.50", "address\t")
        client = self._client(self._network(FakeHelper(status=reading)))
        self.assertFalse(client.get("/api/network").json["connected"])

    def test_says_so_where_the_helper_is_not_installed(self):
        client = self._client(self._network(FakeHelper()))
        status = client.get("/api/network").json

        self.assertFalse(status["available"])
        # The saved configuration still comes back: it is read here, not
        # through the helper.
        self.assertEqual(status["config"]["ssid"], "")

    def test_the_saved_configuration_comes_back_without_the_key(self):
        network = self._network(FakeHelper(status=STATUS))
        network.save(network.parse({"ssid": "Unify", "passphrase": "hunter22"}))

        config = self._client(network).get("/api/network").json["config"]
        self.assertEqual(config["ssid"], "Unify")
        self.assertTrue(config["secured"])
        self.assertNotIn("passphrase", config)
        self.assertNotIn("hunter22", str(config))

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def test_saving_writes_the_configuration_and_applies_it(self):
        helper = FakeHelper(apply="")
        network = self._network(helper)

        response = self._client(network).put(
            "/api/network",
            json={
                "ssid": "Unify",
                "passphrase": "hunter22",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "dns1": "1.1.1.1",
                    "dns2": "8.8.8.8",
                },
            },
        )
        self.assertEqual(response.status_code, 200)

        saved = network.load()
        self.assertEqual(saved["SSID"], "Unify")
        self.assertEqual(saved["PASSPHRASE"], "hunter22")
        self.assertEqual(saved["IPV4_MODE"], "static")
        self.assertEqual(saved["IPV4_ADDRESS"], "192.168.1.50")
        self.assertEqual(saved["IPV4_DNS2"], "8.8.8.8")

        # The apply is queued, not run inside the request: the answer has
        # to leave before the interface goes down under it.
        self.assertEqual(helper.calls, [])
        network.apply()
        self.assertEqual(helper.calls[-1][-1], "apply")

    def test_a_dhcp_configuration_clears_the_static_fields(self):
        network = self._network(FakeHelper(apply=""))
        network.save(
            network.parse(
                {
                    "ssid": "Unify",
                    "passphrase": "hunter22",
                    "ipv4": {
                        "mode": "static",
                        "address": "192.168.1.50",
                        "netmask": "255.255.255.0",
                    },
                }
            )
        )
        network.save(network.parse({"ssid": "Unify", "ipv4": {"mode": "dhcp"}}))

        saved = network.load()
        self.assertEqual(saved["IPV4_MODE"], "dhcp")
        # Left behind, they would be applied again the next time the mode
        # went back to static without one being given.
        self.assertEqual(saved["IPV4_ADDRESS"], "")
        self.assertEqual(saved["IPV4_NETMASK"], "")

    def test_no_key_at_all_keeps_the_one_already_saved(self):
        # The key is never sent back out, so there is nothing for KBRD-WEB
        # to put in the field and leave alone — omitting it has to mean
        # "don't touch it", or changing the IPv4 mode would need the key
        # retyped.
        network = self._network(FakeHelper(apply=""))
        network.save(network.parse({"ssid": "Unify", "passphrase": "hunter22"}))
        network.save(network.parse({"ssid": "Unify", "ipv4": {"mode": "dhcp"}}))

        self.assertEqual(network.load()["PASSPHRASE"], "hunter22")

    def test_an_open_network_is_saved_without_a_key(self):
        network = self._network(FakeHelper(apply=""))
        network.save(network.parse({"ssid": "Unify", "passphrase": "hunter22"}))

        # An empty key is an answer, not a silence: it says the network
        # is open, and clears whatever was stored.
        network.save(network.parse({"ssid": "Guest", "passphrase": ""}))

        self.assertEqual(network.load()["PASSPHRASE"], "")
        self.assertFalse(network.status()["config"]["secured"])

    def test_every_saved_value_is_one_line(self):
        # The file is read back by a shell, line by line (see
        # `/usr/bin/kbrd-network`): a value carrying a newline would
        # become a line of its own there.
        network = self._network()
        for value in ("Uni\nfy", "Uni\rfy", "Uni\x00fy"):
            with self.subTest(value=value):
                with self.assertRaises(NetworkError):
                    network.parse({"ssid": value})

    # ------------------------------------------------------------------
    # What is refused
    # ------------------------------------------------------------------

    def test_refuses_a_configuration_that_would_strand_the_keyboard(self):
        client = self._client(self._network())
        cases = {
            "no ssid": {},
            "empty ssid": {"ssid": ""},
            "ssid too long": {"ssid": "x" * 33},
            "key too short": {"ssid": "Unify", "passphrase": "short"},
            "key too long": {"ssid": "Unify", "passphrase": "x" * 64},
            "unknown mode": {"ssid": "Unify", "ipv4": {"mode": "auto"}},
            "static with no address": {
                "ssid": "Unify",
                "ipv4": {"mode": "static", "netmask": "255.255.255.0"},
            },
            "static with no netmask": {
                "ssid": "Unify",
                "ipv4": {"mode": "static", "address": "192.168.1.50"},
            },
            "address that is not one": {
                "ssid": "Unify",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.300",
                    "netmask": "255.255.255.0",
                },
            },
            "netmask with a hole in it": {
                "ssid": "Unify",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.0.255.0",
                },
            },
            "gateway that is not one": {
                "ssid": "Unify",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.255.255.0",
                    "gateway": "not-an-address",
                },
            },
        }
        for name, body in cases.items():
            with self.subTest(name):
                response = client.put("/api/network", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json)

        # Nothing refused is half-written: the file is only opened once
        # every value has been checked.
        self.assertFalse(os.path.exists(self.config_path))

    def test_accepts_a_static_configuration_without_a_gateway(self):
        # A keyboard on a network with no route out still answers on it.
        network = self._network(FakeHelper(apply=""))
        settings = network.parse(
            {
                "ssid": "Unify",
                "passphrase": "hunter22",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.255.255.0",
                },
            }
        )
        self.assertEqual(settings["IPV4_GATEWAY"], "")
        self.assertEqual(settings["IPV4_DNS1"], "")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def test_lists_what_the_radio_saw(self):
        helper = FakeHelper(scan="Unify\tpsk\nGuest\topen\nUnify\tpsk\n")
        networks = self._client(self._network(helper)).post(
            "/api/network/scan"
        ).json["networks"]

        # One entry per SSID: the same network is seen on more than one
        # band, and the list is there to be picked from.
        self.assertEqual(
            networks,
            [
                {"ssid": "Unify", "security": "psk"},
                {"ssid": "Guest", "security": "open"},
            ],
        )

    def test_a_scan_that_cannot_run_is_an_empty_list(self):
        client = self._client(self._network(FakeHelper()))
        self.assertEqual(client.post("/api/network/scan").json["networks"], [])


if __name__ == "__main__":
    unittest.main()
