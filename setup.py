from setuptools import setup, find_packages

setup(
    name="kbrd-api",
    version="1.0.0",
    packages=find_packages(include=["kbrd_api", "kbrd_api.*"]),
    entry_points={
        "console_scripts": [
            "kbrd-api = kbrd_api.main:main",
        ],
    },
)