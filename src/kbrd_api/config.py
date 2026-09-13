from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8081
    db_path: str = "/data/sqlite/kbrd.db"
    media_dir: str = "/data/media"
    font_dir: str = "/data/fonts"
    bundled_font_dir: str = "/usr/share/kbrd/fonts"
    # Also on the data partition, and for the same reason as the
    # database: the Wi-Fi the keyboard was told to join has to survive
    # a `make flash`. Read back at every boot by
    # `/usr/bin/kbrd-network` (see `api/network.py`).
    network_config_path: str = "/data/network/network.conf"
