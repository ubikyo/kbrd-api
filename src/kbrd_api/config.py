from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8081
    db_path: str = "/data/sqlite/kbrd.db"
