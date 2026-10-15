import importlib
import sqlite3
import sys
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_temperature_approval.db"
    monkeypatch.setenv("TEMPERATURE_DB_PATH", str(db_path))
    monkeypatch.setenv("TEMPERATURE_AUTH_SECRET", "test-secret-for-pytest")
    database = importlib.import_module("backend.database")
    main = importlib.import_module("backend.main")
    importlib.reload(database)
    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client, db_path


def auth_headers(client, username="admin", password="admin123"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['auth_token']}"}


def test_database_schema_contains_required_business_tables(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, db_path = next(client_gen)
    try:
        assert client.get("/api/dashboard").status_code == 401
        assert client.get("/api/dashboard", headers=auth_headers(client)).status_code == 200
        with sqlite3.connect(db_path) as connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required = {
                "roles",
                "users",
                "temperature_devices",
                "temperature_points",
                "parameter_change_requests",
                "approval_records",
                "parameter_change_logs",
                "temperature_readings",
                "alarm_events",
            }
            assert required.issubset(table_names)
            for table in required:
                columns = {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                assert {"id", "created_at", "updated_at", "deleted_at"}.issubset(columns)
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_auth_and_approval_flow_creates_logs(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _ = next(client_gen)
    try:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        assert login.status_code == 200
        assert login.json()["role_code"] == "admin"
        admin_headers = {"Authorization": f"Bearer {login.json()['auth_token']}"}

        device = client.post(
            "/api/devices",
            headers=admin_headers,
            json={
                "device_code": "TCU-TDD-001",
                "device_name": "TDD temperature cabinet",
                "workshop": "A1",
                "location": "east side",
                "status": "running",
                "target_temperature": 65.0,
                "safe_min": 50.0,
                "safe_max": 80.0,
                "owner": "设备管理员",
            },
        )
        assert device.status_code == 201
        device_id = device.json()["id"]

        user_headers = auth_headers(client, "user1", "123456")
        assert client.get("/api/users", headers=user_headers).status_code == 403
        assert client.get("/api/approvals", headers=user_headers).status_code == 403

        request = client.post(
            "/api/change-requests",
            headers=user_headers,
            json={
                "device_id": device_id,
                "parameter_name": "target_temperature",
                "old_value": "65.0",
                "new_value": "70.0",
                "reason": "batch process temperature adjustment",
                "applicant_id": 1,
                "priority": "normal",
            },
        )
        assert request.status_code == 201
        request_id = request.json()["id"]

        forbidden_approval = client.post(
            f"/api/approvals/{request_id}",
            headers=user_headers,
            json={"approver_id": 2, "action": "approved", "comment": "not allowed"},
        )
        assert forbidden_approval.status_code == 403

        approval = client.post(
            f"/api/approvals/{request_id}",
            headers=admin_headers,
            json={"approver_id": 1, "action": "approved", "comment": "approved"},
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "approved"

        logs = client.get(
            "/api/change-logs",
            headers=user_headers,
            params={"request_id": request_id},
        )
        assert logs.status_code == 200
        assert len(logs.json()["items"]) == 1
        own_requests = client.get("/api/change-requests", headers=user_headers)
        assert own_requests.status_code == 200
        assert {item["applicant_id"] for item in own_requests.json()["items"]} == {2}
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_frontend_has_required_real_pages():
    pages = sorted(ROOT.joinpath("frontend").glob("*.html"))
    page_names = {page.name for page in pages}
    required_pages = {
        "login.html",
        "dashboard.html",
        "users.html",
        "devices.html",
        "points.html",
        "change_requests.html",
        "approvals.html",
        "change_logs.html",
        "data_query.html",
        "visualization.html",
        "analysis.html",
        "settings.html",
        "monitor.html",
    }
    assert required_pages.issubset(page_names)
    assert len(pages) >= 10
    for page in required_pages:
        content = ROOT.joinpath("frontend", page).read_text(encoding="utf-8")
        assert "app.js" in content


def test_analysis_and_delivery_assets_exist():
    expected = [
        ROOT / "database" / "schema.sql",
        ROOT / "database" / "data.sql",
        ROOT / "scripts" / "seed_data.py",
        ROOT / "analysis" / "temperature_analysis.ipynb",
        ROOT / "docs" / "report.md",
        ROOT / "docs" / "ppt_outline.md",
        ROOT / "README.md",
        ROOT / "start_api.bat",
    ]
    for path in expected:
        assert path.exists(), f"missing {path}"


def test_fastapi_docs_link_works_when_html_is_opened_locally():
    app_js = ROOT.joinpath("frontend", "app.js").read_text(encoding="utf-8")
    assert 'href="/docs"' not in app_js
    assert "http://127.0.0.1:8008/docs" in app_js


def test_excel_png_and_report_export_entrypoints_exist(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _ = next(client_gen)
    try:
        headers = auth_headers(client)
        for path in [
            "/api/export/devices.xlsx",
            "/api/export/readings.xlsx",
            "/api/export/device-template.xlsx",
            "/api/export/points.xlsx",
            "/api/export/point-template.xlsx",
            "/api/export/analysis-report.csv",
        ]:
            response = client.get(path, headers=headers)
            assert response.status_code == 200
            assert len(response.content) > 100
            assert "attachment" in response.headers["content-disposition"]

        app_js = ROOT.joinpath("frontend", "app.js").read_text(encoding="utf-8")
        for expected in [
            "exportDevices",
            "exportReadings",
            "exportDeviceTemplate",
            "exportPoints",
            "exportPointTemplate",
            "downloadChart",
            "analysis-report.csv",
        ]:
            assert expected in app_js
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_strict_assignment_features_are_exposed(tmp_path, monkeypatch):
    client_gen = make_client(tmp_path, monkeypatch)
    client, _ = next(client_gen)
    try:
        headers = auth_headers(client)
        assert client.post("/api/users/2/reset-password", headers=headers, json={"password": "654321"}).status_code == 200
        assert client.post("/api/users/2/toggle-status", headers=headers).status_code == 200
        assert client.put(
            "/api/users/2",
            headers=headers,
            json={"username": "user1", "real_name": "普通用户", "role_id": 2, "status": "enabled"},
        ).status_code == 200

        assert client.put(
            "/api/devices/1",
            headers=headers,
            json={
                "device_code": "TCU-A-001",
                "device_name": "A线热处理温控柜",
                "workshop": "A车间",
                "location": "热处理线",
                "status": "running",
                "target_temperature": 68,
                "safe_min": 55,
                "safe_max": 82,
                "owner": "设备管理员",
            },
        ).status_code == 200

        point = client.post(
            "/api/points",
            headers=headers,
            json={
                "device_id": 1,
                "point_code": "TP-TDD-STRICT",
                "point_name": "strict point",
                "parameter_name": "temperature",
                "unit": "C",
                "upper_limit": 90,
                "lower_limit": 40,
            },
        )
        assert point.status_code == 201
        point_id = point.json()["id"]
        updated = client.put(
            f"/api/points/{point_id}",
            headers=headers,
            json={
                "device_id": 1,
                "point_code": "TP-TDD-STRICT",
                "point_name": "strict point updated",
                "parameter_name": "temperature",
                "unit": "C",
                "upper_limit": 91,
                "lower_limit": 39,
            },
        )
        assert updated.status_code == 200
        assert client.delete(f"/api/points/{point_id}", headers=headers).status_code == 200

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"])
        sheet.append(["TCU-A-001", "TP-TDD-IMPORT", "import point", "temperature", "C", 92, 38])
        buffer = BytesIO()
        workbook.save(buffer)
        response = client.post(
            "/api/import/points.xlsx",
            headers=headers,
            files={"file": ("points.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200

        readings = client.get(
            "/api/readings",
            headers=headers,
            params={"device_id": 1, "status_filter": "normal", "start": "2000-01-01", "end": "2999-12-31", "page": 1, "page_size": 5},
        )
        assert readings.status_code == 200
        assert readings.json()["page"] == 1
        assert readings.json()["page_size"] == 5

        insights = client.get("/api/analytics/insights", headers=headers)
        assert insights.status_code == 200
        body = insights.json()
        assert {"statistics", "anomaly_detection", "trend_prediction"}.issubset(body)

        app_js = ROOT.joinpath("frontend", "app.js").read_text(encoding="utf-8")
        for expected in [
            "resetPassword",
            "toggleUserStatus",
            "editUser",
            "editDevice",
            "editPoint",
            "importDevices",
            "importPoints",
            "startDate",
            "endDate",
            "statusFilter",
            "loadVisualization",
            "viz-command",
            "riskChart",
            "priorityChart",
            "alarmTimelineChart",
            "trend_prediction",
        ]:
            assert expected in app_js
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
