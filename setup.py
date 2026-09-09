from setuptools import setup, find_packages

setup(
    name="kbrd-api",
    version="1.0.0",

    package_dir={"": "src"},
    packages=find_packages(where="src"),

    # `openapi.yaml` sits beside the package's modules and is served as a
    # static file by `api/docs.py` — it has to travel with an installed
    # copy, not just with the source tree the Makefile rsyncs.
    package_data={"kbrd_api": ["openapi.yaml"]},

    # Test-only. PyYAML is *not* a runtime dependency: Swagger UI parses
    # the spec in the browser, so nothing on the device ever reads it —
    # only `tests/test_openapi.py`, which checks the spec against the
    # app's own URL map, needs to.
    extras_require={"test": ["pytest", "pyyaml"]},

    entry_points={
        "console_scripts": [
            "kbrd-api = kbrd_api.main:main",
        ],
    },
)