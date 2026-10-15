"""安全与健壮性回归测试：口令哈希、令牌有效期、审批校验、并发与权限边界、按设备趋势预测。"""

import importlib
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_hardening.db"
    monkeypatch.setenv("TEMPERATURE_DB_PATH", str(db_path))
    monkeypatch.setenv("TEMPERATURE_AUTH_SECRET", "test-secret-for-pytest")
    monkeypatch.setenv("TEMPERATURE_TOKEN_TTL_SECONDS", "7200")
    database = importlib.import_module("backend.database")
    main = importlib.import_module("backend.main")
    importlib.reload(database)
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, db_path, main


def auth_headers(client, username="admin", password="admin123"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['auth_token']}"}


def create_device(client, headers, target=65.0, safe_min=50.0, safe_max=80.0, code="TCU-SEC-001"):
    response = client.post(
        "/api/devices",
        headers=headers,
        json={
            "device_code": code,
            "device_name": "安全测试温控柜",
            "workshop": "测试车间",
            "location": "测试线",
            "status": "running",
            "target_temperature": target,
            "safe_min": safe_min,
            "safe_max": safe_max,
            "owner": "设备管理员",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_request(client, headers, device_id, parameter, old_value, new_value, reason="安全回归测试"):
    response = client.post(
        "/api/change-requests",
        headers=headers,
        json={
            "device_id": device_id,
            "parameter_name": parameter,
            "old_value": str(old_value),
            "new_value": str(new_value),
            "reason": reason,
            "priority": "normal",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_auth_secret_is_required_and_checked(tmp_path):
    """以真实子进程验证：缺少或过短的 TEMPERATURE_AUTH_SECRET 时服务拒绝启动。"""

    def start_with_secret(secret):
        env = dict(os.environ)
        env.pop("TEMPERATURE_AUTH_SECRET", None)
        env["TEMPERATURE_DB_PATH"] = str(tmp_path / "secret.db")
        if secret is not None:
            env["TEMPERATURE_AUTH_SECRET"] = secret
        return subprocess.run(
            [sys.executable, "-c", "import backend.main"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    missing = start_with_secret(None)
    assert missing.returncode != 0
    assert "TEMPERATURE_AUTH_SECRET" in missing.stderr

    weak = start_with_secret("short")
    assert weak.returncode != 0
    assert "TEMPERATURE_AUTH_SECRET" in weak.stderr

    strong = start_with_secret(secrets.token_hex(32))
    assert strong.returncode == 0, strong.stderr


def test_passwords_are_hashed_and_never_returned(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, db_path, _ = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        assert client.post(
            "/api/users",
            headers=admin_headers,
            json={"username": "sec_user", "real_name": "安全测试用户", "password": "abcdef", "role_id": 2},
        ).status_code == 201

        with sqlite3.connect(db_path) as connection:
            stored = [row[0] for row in connection.execute("SELECT password FROM users")]
        assert stored, "users table should not be empty"
        for value in stored:
            assert value.startswith("pbkdf2_sha256$120000$"), value
            assert value not in {"admin123", "123456", "abcdef"}

        assert auth_headers(client, "sec_user", "abcdef")
        assert client.post(
            "/api/auth/login", json={"username": "sec_user", "password": "wrong"}
        ).status_code == 401

        updated = client.put(
            "/api/users/2",
            headers=admin_headers,
            json={"username": "user1", "real_name": "普通用户", "role_id": 2, "status": "enabled", "password": "123456"},
        )
        assert updated.status_code == 200
        assert "password" not in updated.json()

        assert client.post(
            "/api/users",
            headers=admin_headers,
            json={"username": "weak", "real_name": "弱口令", "password": "123", "role_id": 2},
        ).status_code == 400
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_token_expiry_and_tampering_are_rejected(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _, main = next(client_gen)
    try:
        good = auth_headers(client)
        assert client.get("/api/dashboard", headers=good).status_code == 200

        expired = main.issue_auth_token(1, ttl_seconds=-10)
        assert client.get(
            "/api/dashboard", headers={"Authorization": f"Bearer {expired}"}
        ).status_code == 401

        payload, signature = good["Authorization"].removeprefix("Bearer ").split(".", 1)
        tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
        assert client.get(
            "/api/dashboard",
            headers={"Authorization": f"Bearer {tampered_payload}.{signature}"},
        ).status_code == 401

        assert client.get(
            "/api/dashboard",
            headers={"Authorization": f"Bearer {payload}.{'0' * len(signature)}"},
        ).status_code == 401

        assert client.get("/api/dashboard").status_code == 401
        assert client.get("/api/dashboard", headers={"Authorization": "Bearer garbage"}).status_code == 401
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_approval_rejects_stale_old_value(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _, _ = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        user_headers = auth_headers(client, "user1", "123456")
        device_id = create_device(client, admin_headers, target=65.0)

        request_id = create_request(client, user_headers, device_id, "target_temperature", 65.0, 70.0)

        moved = client.put(
            f"/api/devices/{device_id}",
            headers=admin_headers,
            json={
                "device_code": "TCU-SEC-001",
                "device_name": "安全测试温控柜",
                "workshop": "测试车间",
                "location": "测试线",
                "status": "running",
                "target_temperature": 66.0,
                "safe_min": 50.0,
                "safe_max": 80.0,
                "owner": "设备管理员",
            },
        )
        assert moved.status_code == 200

        stale = client.post(
            f"/api/approvals/{request_id}",
            headers=admin_headers,
            json={"action": "approved", "comment": "原值已过期"},
        )
        assert stale.status_code == 409
        assert client.get(f"/api/change-logs", headers=user_headers, params={"request_id": request_id}).json()["items"] == []
        device = client.get(f"/api/devices", headers=admin_headers, params={"q": "TCU-SEC-001"}).json()["items"][0]
        assert device["target_temperature"] == 66.0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_approval_rejects_out_of_range_and_invalid_values(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _, _ = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        user_headers = auth_headers(client, "user1", "123456")
        device_id = create_device(client, admin_headers, target=65.0, safe_min=50.0, safe_max=80.0)

        cases = [
            ("target_temperature", 65.0, 90.0, "目标温度必须位于安全区间"),
            ("target_temperature", 65.0, 40.0, "目标温度必须位于安全区间"),
            ("target_temperature", 65.0, 65.0, "新值不能与原值相同"),
            ("target_temperature", 65.0, "abc", "参数值必须是数字"),
            ("target_temperature", 65.0, "nan", "参数值必须是有限数字"),
            ("safe_min", 50.0, 70.0, "安全下限必须小于目标温度"),
            ("safe_max", 80.0, 60.0, "安全上限必须大于目标温度"),
        ]
        for parameter, old_value, new_value, expected in cases:
            request_id = create_request(client, user_headers, device_id, parameter, old_value, new_value)
            response = client.post(
                f"/api/approvals/{request_id}",
                headers=admin_headers,
                json={"action": "approved", "comment": "should fail"},
            )
            assert response.status_code == 400, (parameter, new_value, response.text)
            assert expected in response.json()["detail"], (parameter, new_value, response.text)

        request = client.get(
            "/api/change-requests", headers=user_headers, params={"status_filter": "pending"}
        ).json()["items"]
        assert {item["id"] for item in request}, "pending requests should stay pending after rejections"
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_approval_cannot_be_applied_twice(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, db_path, _ = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        user_headers = auth_headers(client, "user1", "123456")
        device_id = create_device(client, admin_headers, target=65.0)
        request_id = create_request(client, user_headers, device_id, "target_temperature", 65.0, 70.0)

        first = client.post(
            f"/api/approvals/{request_id}",
            headers=admin_headers,
            json={"action": "approved", "comment": "通过"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/approvals/{request_id}",
            headers=admin_headers,
            json={"action": "approved", "comment": "重复审批"},
        )
        assert second.status_code == 400

        with sqlite3.connect(db_path) as connection:
            approvals = connection.execute(
                "SELECT COUNT(*) FROM approval_records WHERE request_id = ?", (request_id,)
            ).fetchone()[0]
            logs = connection.execute(
                "SELECT COUNT(*) FROM parameter_change_logs WHERE request_id = ?", (request_id,)
            ).fetchone()[0]
        assert approvals == 1
        assert logs == 1
        device = client.get(f"/api/devices", headers=admin_headers, params={"q": "TCU-SEC-001"}).json()["items"][0]
        assert device["target_temperature"] == 70.0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_concurrent_approvals_produce_single_winner(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, db_path, main = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        user_headers = auth_headers(client, "user1", "123456")
        device_id = create_device(client, admin_headers, target=65.0)
        request_id = create_request(client, user_headers, device_id, "target_temperature", 65.0, 70.0)

        clients = [TestClient(main.app), TestClient(main.app)]
        barrier = threading.Barrier(len(clients))
        results: list[int] = []

        def submit(thread_client: TestClient) -> None:
            barrier.wait()
            response = thread_client.post(
                f"/api/approvals/{request_id}",
                headers=admin_headers,
                json={"action": "approved", "comment": "并发审批"},
            )
            results.append(response.status_code)

        threads = [threading.Thread(target=submit, args=(thread_client,)) for thread_client in clients]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        for thread_client in clients:
            thread_client.close()

        assert sorted(results) == [200, 400], results
        with sqlite3.connect(db_path) as connection:
            approvals = connection.execute(
                "SELECT COUNT(*) FROM approval_records WHERE request_id = ?", (request_id,)
            ).fetchone()[0]
            logs = connection.execute(
                "SELECT COUNT(*) FROM parameter_change_logs WHERE request_id = ?", (request_id,)
            ).fetchone()[0]
            device = connection.execute(
                "SELECT target_temperature FROM temperature_devices WHERE id = ?", (device_id,)
            ).fetchone()[0]
        assert approvals == 1
        assert logs == 1
        assert device == 70.0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_permission_boundaries_for_anonymous_and_normal_users(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _, _ = next(client_gen)
    try:
        protected_paths = ["/api/dashboard", "/api/devices", "/api/readings", "/api/users", "/api/approvals"]
        for path in protected_paths:
            assert client.get(path).status_code == 401, path

        user_headers = auth_headers(client, "user1", "123456")
        admin_only = [
            ("get", "/api/users", None),
            ("post", "/api/devices", {
                "device_code": "TCU-FORBIDDEN",
                "device_name": "越权设备",
                "workshop": "A",
                "location": "B",
                "status": "running",
                "target_temperature": 60.0,
                "safe_min": 50.0,
                "safe_max": 70.0,
                "owner": "设备管理员",
            }),
            ("delete", "/api/devices/1", None),
            ("put", "/api/points/1", {
                "device_id": 1,
                "point_code": "TP-001-A",
                "point_name": "越权修改",
                "parameter_name": "temperature",
                "unit": "C",
                "upper_limit": 82.0,
                "lower_limit": 55.0,
            }),
            ("post", "/api/readings", {
                "device_id": 1,
                "temperature": 60.0,
                "target_temperature": 68.0,
            }),
        ]
        for method, path, payload in admin_only:
            response = client.request(method, path, headers=user_headers, json=payload)
            assert response.status_code == 403, (method, path, response.text)

        own_data = client.get("/api/readings", headers=user_headers)
        assert own_data.status_code == 200
        others = client.get("/api/change-requests", headers=user_headers).json()["items"]
        assert all(item["applicant_id"] == 2 for item in others)

        admin_headers = auth_headers(client)
        assert client.post("/api/users/2/toggle-status", headers=admin_headers).status_code == 200
        assert client.post(
            "/api/auth/login", json={"username": "user1", "password": "123456"}
        ).status_code == 401
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_trend_prediction_is_grouped_by_device(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _, _ = next(client_gen)
    try:
        admin_headers = auth_headers(client)
        device_a = create_device(
            client, admin_headers, target=40.0, safe_min=20.0, safe_max=60.0, code="TCU-FC-A"
        )
        device_b = create_device(
            client, admin_headers, target=90.0, safe_min=70.0, safe_max=110.0, code="TCU-FC-B"
        )
        for index in range(12):
            for device_id, base in ((device_a, 40.0), (device_b, 90.0)):
                assert client.post(
                    "/api/readings",
                    headers=admin_headers,
                    json={
                        "device_id": device_id,
                        "temperature": base + (index % 3) - 1,
                        "target_temperature": base,
                        "recorded_at": f"2026-10-01 10:{index:02d}:00",
                    },
                ).status_code == 201

        insights = client.get("/api/analytics/insights", headers=admin_headers).json()
        prediction = insights["trend_prediction"]
        assert prediction["window"] == 12
        assert "按设备分组" in prediction["method"]

        devices = {item["device_id"]: item for item in prediction["devices"]}
        assert {device_a, device_b}.issubset(devices)
        assert abs(devices[device_a]["next_temperature_estimate"] - 40.0) <= 2.0
        assert abs(devices[device_b]["next_temperature_estimate"] - 90.0) <= 2.0
        assert devices[device_a]["next_temperature_estimate"] != devices[device_b]["next_temperature_estimate"]

        expected_overall = round(
            sum(item["next_temperature_estimate"] for item in prediction["devices"]) / len(prediction["devices"]), 2
        )
        assert prediction["next_temperature_estimate"] == expected_overall
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
