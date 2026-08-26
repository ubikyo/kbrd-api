from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8081
    db_path: str = "/data/sqlite/kbrd.db"
    media_dir: str = "/data/media"
    font_dir: str = "/data/fonts"
    bundled_font_dir: str = "/usr/share/kbrd/fonts"
