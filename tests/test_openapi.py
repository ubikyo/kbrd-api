import re
import unittest

from kbrd_api.api.docs import SPEC_PATH
from kbrd_api.config import Config

try:
    from kbrd_api.main import create_app
except ModuleNotFoundError:
    create_app = None

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

# Flask registers these on every rule without anyone writing them, and a
# spec doesn't describe them.
IMPLICIT_METHODS = {"HEAD", "OPTIONS"}

# Flask's own static route, which exists whether or not the app serves
# anything from it — not part of KBRD-API's contract.
IGNORED_ENDPOINTS = {"static"}

DOCUMENTED_METHODS = {"get", "post", "put", "delete", "patch"}

# `<int:layer_id>` / `<path:application_id>` / `<key_ref>` -> `{name}`:
# Flask spells a converter into the rule, OpenAPI keeps it in the
# parameter's own schema.
CONVERTER = re.compile(r"<(?:[^:<>]+:)?([^<>]+)>")


def openapi_path(rule: str) -> str:
    return CONVERTER.sub(r"{\1}", rule)


@unittest.skipIf(create_app is None, "Flask is not installed")
@unittest.skipIf(yaml is None, "PyYAML is not installed")
class OpenApiSpecTest(unittest.TestCase):
    """Holds `openapi.yaml` to what the app actually serves.

    The spec is written by hand (see its own header for why), so nothing
    but this stops it drifting. It compares in both directions on purpose:
    a route added without documentation is the common case, but a path
    left in the spec after its route was deleted is the one nobody
    notices.
    """

    @classmethod
    def setUpClass(cls):
        app, _ = create_app(Config(db_path=":memory:"))
        cls.routes = {}
        for rule in app.url_map.iter_rules():
            if rule.endpoint in IGNORED_ENDPOINTS:
                continue
            methods = {
                method.lower()
                for method in rule.methods
                if method not in IMPLICIT_METHODS
            }
            cls.routes.setdefault(openapi_path(str(rule)), set()).update(methods)

        cls.spec = yaml.safe_load(SPEC_PATH.read_text())
        cls.documented = {
            path: {key for key in item if key in DOCUMENTED_METHODS}
            for path, item in cls.spec["paths"].items()
        }

    def test_every_route_is_documented(self):
        missing = sorted(set(self.routes) - set(self.documented))
        self.assertEqual(
            missing,
            [],
            f"routes with no entry in openapi.yaml: {missing}",
        )

    def test_every_documented_path_exists(self):
        extra = sorted(set(self.documented) - set(self.routes))
        self.assertEqual(
            extra,
            [],
            f"openapi.yaml documents paths no route serves: {extra}",
        )

    def test_methods_match(self):
        for path in sorted(set(self.routes) & set(self.documented)):
            with self.subTest(path=path):
                self.assertEqual(
                    self.documented[path],
                    self.routes[path],
                    f"{path}: documented methods and served methods differ",
                )

    def test_every_operation_has_an_id_a_summary_and_a_tag(self):
        seen = set()
        for path, item in self.spec["paths"].items():
            for method in sorted(set(item) & DOCUMENTED_METHODS):
                operation = item[method]
                with self.subTest(path=path, method=method):
                    self.assertIn("summary", operation)
                    self.assertIn("tags", operation)
                    operation_id = operation.get("operationId")
                    self.assertIsNotNone(operation_id)
                    # Swagger UI builds its anchors out of these, so a
                    # duplicate silently makes one operation unlinkable.
                    self.assertNotIn(operation_id, seen)
                    seen.add(operation_id)

    def test_every_tag_used_is_declared(self):
        declared = {tag["name"] for tag in self.spec["tags"]}
        used = {
            tag
            for item in self.spec["paths"].values()
            for method, operation in item.items()
            if method in DOCUMENTED_METHODS
            for tag in operation.get("tags", [])
        }
        self.assertEqual(used - declared, set())

    def test_the_spec_is_served(self):
        app, _ = create_app(Config(db_path=":memory:"))
        app.testing = True
        client = app.test_client()

        spec = client.get("/api/openapi.yaml")
        self.assertEqual(spec.status_code, 200)
        self.assertEqual(
            yaml.safe_load(spec.get_data(as_text=True))["info"]["title"],
            "KBRD-API",
        )

        docs = client.get("/api/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("/api/openapi.yaml", docs.get_data(as_text=True))
