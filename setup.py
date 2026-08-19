from setuptools import setup, find_packages

setup(
    name="kbrd-api",
    version="1.0.0",

    package_dir={"": "src"},
    packages=find_packages(where="src"),

    entry_points={
        "console_scripts": [
            "kbrd-api = kbrd_api.main:main",
        ],
    },
)