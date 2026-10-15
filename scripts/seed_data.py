import math
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import get_connection, initialize_database


def seed_readings(connection: sqlite3.Connection, total: int = 1200) -> None:
    existing = connection.execute("SELECT COUNT(*) FROM temperature_readings").fetchone()[0]
    if existing >= total:
        return

    devices = connection.execute(
        "SELECT id, target_temperature FROM temperature_devices WHERE deleted_at IS NULL"
    ).fetchall()
    points = connection.execute(
        "SELECT id, device_id FROM temperature_points WHERE deleted_at IS NULL"
    ).fetchall()
    point_by_device = {}
    for point in points:
        point_by_device.setdefault(point["device_id"], []).append(point["id"])

    start_time = datetime.now() - timedelta(days=20)
    rows = []
    for index in range(total - existing):
        device = devices[index % len(devices)]
        target = float(device["target_temperature"])
        wave = math.sin(index / 18) * 2.8
        noise = random.uniform(-1.4, 1.4)
        spike = 8 if index % 137 == 0 else 0
        value = round(target + wave + noise + spike, 2)
        status = "warning" if abs(value - target) >= 6 else "normal"
        recorded_at = start_time + timedelta(minutes=20 * index)
        point_id = random.choice(point_by_device.get(device["id"], [None]))
        rows.append((device["id"], point_id, value, target, status, recorded_at.isoformat(sep=" ")))

    connection.executemany(
        """
        INSERT INTO temperature_readings
        (device_id, point_id, temperature, target_temperature, status, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def seed_requests(connection: sqlite3.Connection) -> None:
    existing = connection.execute("SELECT COUNT(*) FROM parameter_change_requests").fetchone()[0]
    if existing >= 30:
        return
    devices = connection.execute("SELECT id, target_temperature, safe_max FROM temperature_devices").fetchall()
    rows = []
    for index in range(30 - existing):
        device = devices[index % len(devices)]
        parameter = "target_temperature" if index % 2 == 0 else "safe_max"
        old_value = float(device[parameter])
        new_value = old_value + random.choice([-2, -1, 1, 2])
        status = random.choice(["pending", "approved", "rejected"])
        rows.append(
            (
                device["id"],
                parameter,
                str(old_value),
                str(new_value),
                "批量造数：工艺窗口复核",
                random.choice([2, 3]),
                random.choice(["normal", "high"]),
                status,
            )
        )
    connection.executemany(
        """
        INSERT INTO parameter_change_requests
        (device_id, parameter_name, old_value, new_value, reason, applicant_id, priority, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def seed_alarms(connection: sqlite3.Connection) -> None:
    existing = connection.execute("SELECT COUNT(*) FROM alarm_events").fetchone()[0]
    if existing >= 40:
        return
    warning_readings = connection.execute(
        """
        SELECT device_id, temperature, target_temperature, recorded_at
        FROM temperature_readings
        WHERE status = 'warning'
        LIMIT 40
        """
    ).fetchall()
    rows = [
        (
            row["device_id"],
            "temperature_deviation",
            "warning",
            f"温度 {row['temperature']}C 偏离目标 {row['target_temperature']}C",
            "open" if index % 3 == 0 else "closed",
            row["recorded_at"],
        )
        for index, row in enumerate(warning_readings)
    ]
    connection.executemany(
        """
        INSERT INTO alarm_events
        (device_id, alarm_type, severity, message, status, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def main() -> None:
    initialize_database()
    with get_connection() as connection:
        seed_readings(connection)
        seed_requests(connection)
        seed_alarms(connection)
        connection.commit()
    print("Seed data ready: 1000+ temperature readings and approval records generated.")


if __name__ == "__main__":
    main()
