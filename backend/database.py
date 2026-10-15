import os
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATABASE_DIR = ROOT_DIR / "database"
DEFAULT_DB_PATH = DATABASE_DIR / "temperature.db"


def get_db_path() -> Path:
    configured = os.getenv("TEMPERATURE_DB_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_DB_PATH


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize_database() -> None:
    schema_path = DATABASE_DIR / "schema.sql"
    data_path = DATABASE_DIR / "data.sql"
    with get_connection() as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.executescript(data_path.read_text(encoding="utf-8"))
        connection.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
