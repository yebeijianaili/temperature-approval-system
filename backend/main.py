from contextlib import asynccontextmanager, closing
import base64
import hashlib
import hmac
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path
import secrets
import time
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from openpyxl import Workbook, load_workbook

from backend.database import get_connection, initialize_database, row_to_dict, rows_to_dicts


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


def _load_auth_secret() -> str:
    """认证密钥只允许来自环境变量，不提供代码内默认值，避免弱密钥被直接部署。"""
    secret = os.getenv("TEMPERATURE_AUTH_SECRET", "").strip()
    if len(secret) < 16:
        raise RuntimeError(
            "缺少环境变量 TEMPERATURE_AUTH_SECRET（至少 16 位随机字符串）："
            "请先执行 python -c \"import secrets; print(secrets.token_hex(32))\" "
            "生成密钥并写入环境变量后再启动服务。"
        )
    return secret


AUTH_SECRET = _load_auth_secret()
TOKEN_TTL_SECONDS = max(int(os.getenv("TEMPERATURE_TOKEN_TTL_SECONDS", "7200")), 60)
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login"}
ADMIN_API_PREFIXES = ("/api/users", "/api/approvals", "/api/import")
DEVICE_PARAMETER_COLUMNS = {"target_temperature", "safe_min", "safe_max"}
PBKDF2_ITERATIONS = 120_000
TREND_WINDOW = 12
DEVIATION_WARNING_THRESHOLD = 6.0

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="温控设备参数变更审批系统",
    description="课程项目：FastAPI + SQLite + HTML 的温控审批与数据分析系统",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8008", "http://localhost:8008"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/frontend"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def authenticate_api_request(request: Request, call_next):
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or not path.startswith("/api/")
        or path in PUBLIC_API_PATHS
    ):
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_from_token(token) if token else None
    if user is None or user["status"] != "enabled":
        return JSONResponse(status_code=401, content={"detail": "登录已失效，请重新登录"})
    if is_admin_only_request(request) and user["role_code"] != "admin":
        return JSONResponse(status_code=403, content={"detail": "仅管理员可执行此操作"})
    request.state.user = user
    return await call_next(request)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserPayload(BaseModel):
    username: str
    real_name: str
    password: str = "123456"
    role_id: int = 2
    status: str = "enabled"


class UserUpdatePayload(BaseModel):
    username: str
    real_name: str
    password: str | None = None
    role_id: int = 2
    status: str = "enabled"


class PasswordResetPayload(BaseModel):
    password: str


class DevicePayload(BaseModel):
    device_code: str
    device_name: str
    workshop: str
    location: str
    status: str = "running"
    target_temperature: float
    safe_min: float
    safe_max: float
    owner: str


class PointPayload(BaseModel):
    device_id: int
    point_code: str
    point_name: str
    parameter_name: str = "temperature"
    unit: str = "C"
    upper_limit: float
    lower_limit: float


class ChangeRequestPayload(BaseModel):
    device_id: int
    parameter_name: str
    old_value: str
    new_value: str
    reason: str
    applicant_id: int | None = None
    priority: str = "normal"


class ApprovalPayload(BaseModel):
    approver_id: int | None = None
    action: str = Field(pattern="^(approved|rejected)$")
    comment: str = ""


class ReadingPayload(BaseModel):
    device_id: int
    point_id: int | None = None
    temperature: float
    target_temperature: float
    recorded_at: str | None = None


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    with closing(get_connection()) as connection:
        row = connection.execute(sql, params).fetchone()
        return row_to_dict(row)


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    with closing(get_connection()) as connection:
        rows = connection.execute(sql, params).fetchall()
    return rows_to_dicts(rows)


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希，返回 algorithm$iterations$salt$digest。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较口令哈希；格式不符或算法不符一律返回 False。"""
    try:
        algorithm, iterations, salt, digest = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest)


def issue_auth_token(user_id: int, ttl_seconds: int | None = None) -> str:
    issued_at = int(time.time())
    ttl = TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    payload_data = {"user_id": user_id, "iat": issued_at, "exp": issued_at + ttl}
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_data, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        AUTH_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def get_user_from_token(token: str) -> dict | None:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(
            AUTH_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        user_id = int(claims["user_id"])
        if int(claims["exp"]) <= int(time.time()):
            return None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    return fetch_one(
        """
        SELECT u.id, u.username, u.real_name, u.status, r.role_code, r.role_name
        FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE u.id = ? AND u.deleted_at IS NULL
        """,
        (user_id,),
    )


def request_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def is_admin_only_request(request: Request) -> bool:
    path = request.url.path
    if path.startswith(ADMIN_API_PREFIXES):
        return True
    if request.method in {"POST", "PUT", "DELETE"} and path.startswith(
        ("/api/devices", "/api/points", "/api/readings")
    ):
        return True
    return False


def make_workbook(sheet_name: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 32)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def attachment_response(content: bytes | str, media_type: str, filename: str) -> Response:
    if isinstance(content, str):
        body = content.encode("utf-8-sig")
    else:
        body = content
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def paged_response(table_sql: str, params: tuple[Any, ...], page: int, page_size: int) -> dict:
    offset = (page - 1) * page_size
    count_sql = f"SELECT COUNT(*) AS total FROM ({table_sql}) AS filtered_rows"
    total = fetch_one(count_sql, params)["total"]
    items = fetch_all(f"{table_sql} LIMIT ? OFFSET ?", params + (page_size, offset))
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict:
    user = fetch_one(
        """
        SELECT u.id, u.username, u.real_name, u.status, u.password, r.role_code, r.role_name
        FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE u.username = ? AND u.deleted_at IS NULL
        """,
        (payload.username,),
    )
    if user is None or user["status"] != "enabled":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not verify_password(payload.password, user["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    user.pop("password", None)
    user["auth_token"] = issue_auth_token(user["id"])
    return user


@app.get("/api/dashboard")
def dashboard(request: Request) -> dict:
    current_user = request_user(request)
    with closing(get_connection()) as connection:
        device_total = connection.execute(
            "SELECT COUNT(*) FROM temperature_devices WHERE deleted_at IS NULL"
        ).fetchone()[0]
        if current_user["role_code"] == "admin":
            pending_total = connection.execute(
                "SELECT COUNT(*) FROM parameter_change_requests WHERE status = 'pending' AND deleted_at IS NULL"
            ).fetchone()[0]
        else:
            pending_total = connection.execute(
                """
                SELECT COUNT(*) FROM parameter_change_requests
                WHERE status = 'pending' AND applicant_id = ? AND deleted_at IS NULL
                """,
                (current_user["id"],),
            ).fetchone()[0]
        alarm_total = connection.execute(
            "SELECT COUNT(*) FROM alarm_events WHERE status = 'open' AND deleted_at IS NULL"
        ).fetchone()[0]
        reading_total = connection.execute(
            "SELECT COUNT(*) FROM temperature_readings WHERE deleted_at IS NULL"
        ).fetchone()[0]
        recent_logs = rows_to_dicts(
            connection.execute(
                """
                SELECT l.id, d.device_name, l.parameter_name, l.old_value, l.new_value, l.changed_at
                FROM parameter_change_logs l
                JOIN temperature_devices d ON d.id = l.device_id
                WHERE l.deleted_at IS NULL
                ORDER BY l.changed_at DESC
                LIMIT 6
                """
            ).fetchall()
        )
    return {
        "device_total": device_total,
        "pending_total": pending_total,
        "alarm_total": alarm_total,
        "reading_total": reading_total,
        "recent_logs": recent_logs,
    }


@app.get("/api/users")
def list_users(q: str = "", page: int = 1, page_size: int = 20) -> dict:
    keyword = f"%{q}%"
    sql = """
        SELECT u.id, u.username, u.real_name, u.status, r.role_name, u.created_at
        FROM users u
        JOIN roles r ON r.id = u.role_id
        WHERE u.deleted_at IS NULL AND (u.username LIKE ? OR u.real_name LIKE ?)
        ORDER BY u.id ASC
    """
    return paged_response(sql, (keyword, keyword), page, page_size)


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_user(payload: UserPayload) -> dict:
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (username, real_name, password, role_id, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payload.username, payload.real_name, hash_password(payload.password), payload.role_id, payload.status),
        )
        connection.commit()
        created = fetch_one("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,))
        created.pop("password", None)
        return created


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserUpdatePayload) -> dict:
    if payload.password is not None and len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")
    with closing(get_connection()) as connection:
        current = connection.execute(
            "SELECT password FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        stored_password = (
            hash_password(payload.password)
            if payload.password is not None
            else current["password"]
        )
        connection.execute(
            """
            UPDATE users
            SET username = ?, real_name = ?, password = ?, role_id = ?, status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                payload.username,
                payload.real_name,
                stored_password,
                payload.role_id,
                payload.status,
                user_id,
            ),
        )
        connection.commit()
    updated = fetch_one("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,))
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated.pop("password", None)
    return updated


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int) -> dict:
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            "UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"message": "用户已删除"}


@app.post("/api/users/{user_id}/reset-password")
def reset_user_password(user_id: int, payload: PasswordResetPayload) -> dict:
    if len(payload.password.strip()) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于 6 位")
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET password = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (hash_password(payload.password), user_id),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"message": "密码已重置"}


@app.post("/api/users/{user_id}/toggle-status")
def toggle_user_status(user_id: int) -> dict:
    with closing(get_connection()) as connection:
        user = connection.execute(
            "SELECT status FROM users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        next_status = "disabled" if user["status"] == "enabled" else "enabled"
        connection.execute(
            "UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_status, user_id),
        )
        connection.commit()
    return {"message": "状态已更新", "status": next_status}


@app.get("/api/devices")
def list_devices(q: str = "", page: int = 1, page_size: int = 50) -> dict:
    keyword = f"%{q}%"
    sql = """
        SELECT * FROM temperature_devices
        WHERE deleted_at IS NULL AND (device_code LIKE ? OR device_name LIKE ? OR workshop LIKE ?)
        ORDER BY id ASC
    """
    return paged_response(sql, (keyword, keyword, keyword), page, page_size)


@app.post("/api/devices", status_code=status.HTTP_201_CREATED)
def create_device(payload: DevicePayload) -> dict:
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO temperature_devices
            (device_code, device_name, workshop, location, status, target_temperature, safe_min, safe_max, owner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.device_code,
                payload.device_name,
                payload.workshop,
                payload.location,
                payload.status,
                payload.target_temperature,
                payload.safe_min,
                payload.safe_max,
                payload.owner,
            ),
        )
        connection.commit()
        return fetch_one("SELECT * FROM temperature_devices WHERE id = ?", (cursor.lastrowid,))


@app.put("/api/devices/{device_id}")
def update_device(device_id: int, payload: DevicePayload) -> dict:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            UPDATE temperature_devices
            SET device_code = ?, device_name = ?, workshop = ?, location = ?, status = ?,
                target_temperature = ?, safe_min = ?, safe_max = ?, owner = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                payload.device_code,
                payload.device_name,
                payload.workshop,
                payload.location,
                payload.status,
                payload.target_temperature,
                payload.safe_min,
                payload.safe_max,
                payload.owner,
                device_id,
            ),
        )
        connection.commit()
    updated = fetch_one("SELECT * FROM temperature_devices WHERE id = ? AND deleted_at IS NULL", (device_id,))
    if updated is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    return updated


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int) -> dict:
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            "UPDATE temperature_devices SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (device_id,),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {"message": "设备已删除"}


@app.get("/api/points")
def list_points(device_id: int | None = None, page: int = 1, page_size: int = 50) -> dict:
    where = "p.deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if device_id is not None:
        where += " AND p.device_id = ?"
        params = (device_id,)
    sql = f"""
        SELECT p.*, d.device_name
        FROM temperature_points p
        JOIN temperature_devices d ON d.id = p.device_id
        WHERE {where}
        ORDER BY p.id ASC
    """
    return paged_response(sql, params, page, page_size)


@app.post("/api/points", status_code=status.HTTP_201_CREATED)
def create_point(payload: PointPayload) -> dict:
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO temperature_points
            (device_id, point_code, point_name, parameter_name, unit, upper_limit, lower_limit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.device_id,
                payload.point_code,
                payload.point_name,
                payload.parameter_name,
                payload.unit,
                payload.upper_limit,
                payload.lower_limit,
            ),
        )
        connection.commit()
        return fetch_one("SELECT * FROM temperature_points WHERE id = ?", (cursor.lastrowid,))


@app.put("/api/points/{point_id}")
def update_point(point_id: int, payload: PointPayload) -> dict:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            UPDATE temperature_points
            SET device_id = ?, point_code = ?, point_name = ?, parameter_name = ?,
                unit = ?, upper_limit = ?, lower_limit = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                payload.device_id,
                payload.point_code,
                payload.point_name,
                payload.parameter_name,
                payload.unit,
                payload.upper_limit,
                payload.lower_limit,
                point_id,
            ),
        )
        connection.commit()
    updated = fetch_one("SELECT * FROM temperature_points WHERE id = ? AND deleted_at IS NULL", (point_id,))
    if updated is None:
        raise HTTPException(status_code=404, detail="测点不存在")
    return updated


@app.delete("/api/points/{point_id}")
def delete_point(point_id: int) -> dict:
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            "UPDATE temperature_points SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
            (point_id,),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="测点不存在")
    return {"message": "测点已删除"}


@app.get("/api/change-requests")
def list_change_requests(
    request: Request,
    status_filter: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    current_user = request_user(request)
    params: tuple[Any, ...] = ()
    where = "r.deleted_at IS NULL"
    if current_user["role_code"] != "admin":
        where += " AND r.applicant_id = ?"
        params = (current_user["id"],)
    if status_filter:
        where += " AND r.status = ?"
        params += (status_filter,)
    sql = f"""
        SELECT r.*, d.device_name, u.real_name AS applicant_name
        FROM parameter_change_requests r
        JOIN temperature_devices d ON d.id = r.device_id
        JOIN users u ON u.id = r.applicant_id
        WHERE {where}
        ORDER BY r.created_at DESC
    """
    return paged_response(sql, params, page, page_size)


@app.post("/api/change-requests", status_code=status.HTTP_201_CREATED)
def create_change_request(payload: ChangeRequestPayload, request: Request) -> dict:
    current_user = request_user(request)
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO parameter_change_requests
            (device_id, parameter_name, old_value, new_value, reason, applicant_id, priority, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                payload.device_id,
                payload.parameter_name,
                payload.old_value,
                payload.new_value,
                payload.reason,
                current_user["id"],
                payload.priority,
            ),
        )
        connection.commit()
        return fetch_one("SELECT * FROM parameter_change_requests WHERE id = ?", (cursor.lastrowid,))


def parse_parameter_value(raw: Any) -> float:
    """审批参数值必须是有限数字，拒绝空串、文本与 inf/nan。"""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="参数值必须是数字")
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="参数值必须是有限数字")
    return value


def validate_device_parameter(device: dict, parameter: str, old_value: float, new_value: float) -> None:
    """按设备安全上下限校验新值，并保证 old_value 与设备当前值一致。"""
    target = float(device["target_temperature"])
    safe_min = float(device["safe_min"])
    safe_max = float(device["safe_max"])
    if parameter == "target_temperature":
        if not safe_min <= new_value <= safe_max:
            raise HTTPException(
                status_code=400,
                detail=f"目标温度必须位于安全区间 [{safe_min}, {safe_max}] 之内",
            )
    elif parameter == "safe_min":
        if new_value >= target or new_value >= safe_max:
            raise HTTPException(
                status_code=400,
                detail=f"安全下限必须小于目标温度 {target} 且小于安全上限 {safe_max}",
            )
    elif parameter == "safe_max":
        if new_value <= target or new_value <= safe_min:
            raise HTTPException(
                status_code=400,
                detail=f"安全上限必须大于目标温度 {target} 且大于安全下限 {safe_min}",
            )


@app.post("/api/approvals/{request_id}")
def approve_request(request_id: int, payload: ApprovalPayload, request: Request) -> dict:
    current_user = request_user(request)
    with closing(get_connection()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            change_request = connection.execute(
                "SELECT * FROM parameter_change_requests WHERE id = ? AND deleted_at IS NULL",
                (request_id,),
            ).fetchone()
            if change_request is None:
                raise HTTPException(status_code=404, detail="申请不存在")
            if change_request["status"] != "pending":
                raise HTTPException(status_code=400, detail="申请已处理")

            parameter = change_request["parameter_name"]
            new_value = old_value = None
            if payload.action == "approved":
                new_value = parse_parameter_value(change_request["new_value"])
                old_value = parse_parameter_value(change_request["old_value"])
                if abs(new_value - old_value) < 1e-9:
                    raise HTTPException(status_code=400, detail="新值不能与原值相同")
                if parameter in DEVICE_PARAMETER_COLUMNS:
                    device = connection.execute(
                        "SELECT * FROM temperature_devices WHERE id = ? AND deleted_at IS NULL",
                        (change_request["device_id"],),
                    ).fetchone()
                    if device is None:
                        raise HTTPException(status_code=404, detail="设备不存在")
                    current_value = float(device[parameter])
                    if abs(old_value - current_value) > 1e-9:
                        raise HTTPException(
                            status_code=409,
                            detail=f"申请原值 {old_value:g} 与设备当前值 {current_value:g} 不一致，"
                            "参数可能已被其他审批修改，请刷新后重新提交",
                        )
                    validate_device_parameter(row_to_dict(device), parameter, old_value, new_value)

            connection.execute(
                """
                UPDATE parameter_change_requests
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (payload.action, request_id),
            )
            connection.execute(
                """
                INSERT INTO approval_records (request_id, approver_id, action, comment)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, current_user["id"], payload.action, payload.comment),
            )
            if payload.action == "approved":
                connection.execute(
                    """
                    INSERT INTO parameter_change_logs
                    (request_id, device_id, parameter_name, old_value, new_value, changed_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        change_request["device_id"],
                        parameter,
                        change_request["old_value"],
                        change_request["new_value"],
                        current_user["id"],
                    ),
                )
                if parameter in DEVICE_PARAMETER_COLUMNS:
                    connection.execute(
                        f"""
                        UPDATE temperature_devices
                        SET {parameter} = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (new_value, change_request["device_id"]),
                    )
            connection.commit()
        except HTTPException:
            connection.rollback()
            raise
        except Exception:
            connection.rollback()
            raise
    return fetch_one("SELECT * FROM parameter_change_requests WHERE id = ?", (request_id,))


@app.get("/api/approvals")
def list_approvals(page: int = 1, page_size: int = 50) -> dict:
    sql = """
        SELECT a.*, r.parameter_name, d.device_name, u.real_name AS approver_name
        FROM approval_records a
        JOIN parameter_change_requests r ON r.id = a.request_id
        JOIN temperature_devices d ON d.id = r.device_id
        JOIN users u ON u.id = a.approver_id
        WHERE a.deleted_at IS NULL
        ORDER BY a.approved_at DESC
    """
    return paged_response(sql, (), page, page_size)


@app.get("/api/change-logs")
def list_change_logs(request_id: int | None = None, page: int = 1, page_size: int = 50) -> dict:
    where = "l.deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if request_id is not None:
        where += " AND l.request_id = ?"
        params = (request_id,)
    sql = f"""
        SELECT l.*, d.device_name, u.real_name AS changed_by_name
        FROM parameter_change_logs l
        JOIN temperature_devices d ON d.id = l.device_id
        JOIN users u ON u.id = l.changed_by
        WHERE {where}
        ORDER BY l.changed_at DESC
    """
    return paged_response(sql, params, page, page_size)


@app.get("/api/readings")
def list_readings(
    device_id: int | None = None,
    status_filter: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    page_size: int = 200,
) -> dict:
    where = "r.deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if device_id is not None:
        where += " AND r.device_id = ?"
        params = (device_id,)
    if status_filter:
        where += " AND r.status = ?"
        params += (status_filter,)
    if start:
        where += " AND date(r.recorded_at) >= date(?)"
        params += (start,)
    if end:
        where += " AND date(r.recorded_at) <= date(?)"
        params += (end,)
    sql = f"""
        SELECT r.*, d.device_name
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE {where}
        ORDER BY r.recorded_at DESC
    """
    return paged_response(sql, params, page, page_size)


@app.post("/api/readings", status_code=status.HTTP_201_CREATED)
def create_reading(payload: ReadingPayload) -> dict:
    status_text = (
        "warning"
        if abs(payload.temperature - payload.target_temperature) >= DEVIATION_WARNING_THRESHOLD
        else "normal"
    )
    with closing(get_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO temperature_readings
            (device_id, point_id, temperature, target_temperature, status, recorded_at)
            VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """,
            (
                payload.device_id,
                payload.point_id,
                payload.temperature,
                payload.target_temperature,
                status_text,
                payload.recorded_at,
            ),
        )
        connection.commit()
        return fetch_one("SELECT * FROM temperature_readings WHERE id = ?", (cursor.lastrowid,))


@app.get("/api/alarms")
def list_alarms(status_filter: str = "", page: int = 1, page_size: int = 50) -> dict:
    where = "a.deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if status_filter:
        where += " AND a.status = ?"
        params = (status_filter,)
    sql = f"""
        SELECT a.*, d.device_name
        FROM alarm_events a
        JOIN temperature_devices d ON d.id = a.device_id
        WHERE {where}
        ORDER BY a.occurred_at DESC
    """
    return paged_response(sql, params, page, page_size)


@app.get("/api/analytics/summary")
def analytics_summary() -> dict:
    overview = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM temperature_devices WHERE deleted_at IS NULL) AS device_total,
            (SELECT COUNT(*) FROM temperature_readings WHERE deleted_at IS NULL) AS reading_total,
            (SELECT COUNT(*) FROM parameter_change_requests WHERE deleted_at IS NULL) AS request_total,
            (SELECT COUNT(*) FROM alarm_events WHERE deleted_at IS NULL) AS alarm_total,
            ROUND(AVG(r.temperature), 2) AS avg_temperature,
            ROUND(AVG(ABS(r.temperature - r.target_temperature)), 2) AS avg_deviation,
            ROUND(MAX(ABS(r.temperature - r.target_temperature)), 2) AS max_deviation,
            SUM(CASE WHEN r.status != 'normal' THEN 1 ELSE 0 END) AS abnormal_readings
        FROM temperature_readings r
        WHERE r.deleted_at IS NULL
        """
    )
    status_rows = fetch_all(
        """
        SELECT status, COUNT(*) AS total
        FROM parameter_change_requests
        WHERE deleted_at IS NULL
        GROUP BY status
        """
    )
    alarm_rows = fetch_all(
        """
        SELECT severity, COUNT(*) AS total
        FROM alarm_events
        WHERE deleted_at IS NULL
        GROUP BY severity
        """
    )
    parameter_rows = fetch_all(
        """
        SELECT parameter_name, COUNT(*) AS total
        FROM parameter_change_requests
        WHERE deleted_at IS NULL
        GROUP BY parameter_name
        ORDER BY total DESC
        """
    )
    device_risk_rows = fetch_all(
        """
        SELECT d.device_name,
               ROUND(AVG(r.temperature), 2) AS avg_temperature,
               ROUND(AVG(ABS(r.temperature - r.target_temperature)), 2) AS avg_deviation,
               SUM(CASE WHEN r.status != 'normal' THEN 1 ELSE 0 END) AS abnormal_total,
               COUNT(*) AS reading_total
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE r.deleted_at IS NULL
        GROUP BY d.id, d.device_name
        ORDER BY avg_deviation DESC
        """
    )
    approval_priority_rows = fetch_all(
        """
        SELECT priority, status, COUNT(*) AS total
        FROM parameter_change_requests
        WHERE deleted_at IS NULL
        GROUP BY priority, status
        ORDER BY priority, status
        """
    )
    alarm_timeline_rows = fetch_all(
        """
        SELECT date(occurred_at) AS day, severity, COUNT(*) AS total
        FROM alarm_events
        WHERE deleted_at IS NULL
        GROUP BY date(occurred_at), severity
        ORDER BY day ASC
        """
    )
    return {
        "overview": overview,
        "request_status": status_rows,
        "alarm_severity": alarm_rows,
        "parameters": parameter_rows,
        "device_risk": device_risk_rows,
        "approval_priority": approval_priority_rows,
        "alarm_timeline": alarm_timeline_rows,
    }


@app.get("/api/analytics/time-range")
def analytics_time_range() -> dict:
    row = fetch_one(
        """
        SELECT
            MIN(date(recorded_at)) AS min_date,
            MAX(date(recorded_at)) AS max_date,
            MIN(CAST(strftime('%Y', recorded_at) AS INTEGER)) AS min_year,
            MAX(CAST(strftime('%Y', recorded_at) AS INTEGER)) AS max_year,
            strftime('%m', MIN(recorded_at)) AS min_month,
            strftime('%m', MAX(recorded_at)) AS max_month
        FROM temperature_readings
        WHERE deleted_at IS NULL
        """
    )
    return row or {}


@app.get("/api/analytics/trends")
def analytics_trends(
    device_id: int | None = None,
    start: str = "",
    end: str = "",
    limit: int = Query(default=80, le=500),
) -> dict:
    where = "r.deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if device_id is not None:
        where += " AND r.device_id = ?"
        params = (device_id,)
    if start:
        where += " AND date(r.recorded_at) >= date(?)"
        params += (start,)
    if end:
        where += " AND date(r.recorded_at) <= date(?)"
        params += (end,)
    rows = fetch_all(
        f"""
        SELECT r.recorded_at, r.temperature, r.target_temperature,
               ROUND(r.temperature - r.target_temperature, 2) AS deviation,
               d.device_name
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE {where}
        ORDER BY r.recorded_at DESC
        LIMIT ?
        """,
        params + (limit,),
    )
    rows.reverse()
    return {"items": rows}


@app.get("/api/analytics/insights")
def analytics_insights() -> dict:
    stats = fetch_one(
        """
        SELECT COUNT(*) AS total,
               ROUND(AVG(temperature), 2) AS avg_temperature,
               ROUND(AVG(ABS(temperature - target_temperature)), 2) AS avg_deviation,
               ROUND(MAX(ABS(temperature - target_temperature)), 2) AS max_deviation
        FROM temperature_readings
        WHERE deleted_at IS NULL
        """
    )
    anomalies = fetch_all(
        f"""
        SELECT r.id, d.device_name, r.temperature, r.target_temperature,
               ROUND(ABS(r.temperature - r.target_temperature), 2) AS deviation,
               r.recorded_at
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE r.deleted_at IS NULL AND ABS(r.temperature - r.target_temperature) >= {DEVIATION_WARNING_THRESHOLD}
        ORDER BY deviation DESC
        LIMIT 10
        """
    )
    recent = fetch_all(
        """
        SELECT r.device_id, d.device_name, r.temperature
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE r.deleted_at IS NULL
        ORDER BY r.device_id ASC, r.recorded_at DESC
        """
    )
    # 与 Notebook 保持一致：按设备分组各自取最近 TREND_WINDOW 条读数计算移动平均，
    # 避免不同目标温度设备的读数混在同一窗口里。
    grouped: dict[int, dict[str, Any]] = {}
    for row in recent:
        bucket = grouped.setdefault(
            row["device_id"],
            {"device_id": row["device_id"], "device_name": row["device_name"], "values": []},
        )
        if len(bucket["values"]) < TREND_WINDOW:
            bucket["values"].append(float(row["temperature"]))
    device_forecasts = []
    for bucket in grouped.values():
        values = bucket["values"]
        estimate = round(sum(values) / len(values), 2)
        latest = values[0]
        direction = "up" if latest > estimate else "down" if latest < estimate else "stable"
        device_forecasts.append(
            {
                "device_id": bucket["device_id"],
                "device_name": bucket["device_name"],
                "window": len(values),
                "latest_temperature": round(latest, 2),
                "next_temperature_estimate": estimate,
                "direction": direction,
            }
        )
    overall_estimate = (
        round(sum(item["next_temperature_estimate"] for item in device_forecasts) / len(device_forecasts), 2)
        if device_forecasts
        else None
    )
    return {
        "statistics": stats,
        "anomaly_detection": anomalies,
        "trend_prediction": {
            "method": f"按设备分组，每组最近 {TREND_WINDOW} 条读数移动平均",
            "window": TREND_WINDOW,
            "devices": device_forecasts,
            "next_temperature_estimate": overall_estimate,
        },
    }


@app.get("/api/export/devices.xlsx")
def export_devices_excel() -> Response:
    rows = fetch_all(
        """
        SELECT device_code, device_name, workshop, location, status,
               target_temperature, safe_min, safe_max, owner, created_at
        FROM temperature_devices
        WHERE deleted_at IS NULL
        ORDER BY id ASC
        """
    )
    workbook = make_workbook(
        "温控设备",
        ["设备编号", "设备名称", "车间", "位置", "状态", "目标温度", "安全下限", "安全上限", "负责人", "创建时间"],
        [
            [
                row["device_code"],
                row["device_name"],
                row["workshop"],
                row["location"],
                row["status"],
                row["target_temperature"],
                row["safe_min"],
                row["safe_max"],
                row["owner"],
                row["created_at"],
            ]
            for row in rows
        ],
    )
    return attachment_response(workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "temperature_devices.xlsx")


@app.get("/api/export/readings.xlsx")
def export_readings_excel() -> Response:
    rows = fetch_all(
        """
        SELECT d.device_code, d.device_name, r.temperature, r.target_temperature,
               r.status, r.recorded_at
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        WHERE r.deleted_at IS NULL
        ORDER BY r.recorded_at DESC
        LIMIT 2000
        """
    )
    workbook = make_workbook(
        "温度记录",
        ["设备编号", "设备名称", "实际温度", "目标温度", "状态", "记录时间"],
        [
            [
                row["device_code"],
                row["device_name"],
                row["temperature"],
                row["target_temperature"],
                row["status"],
                row["recorded_at"],
            ]
            for row in rows
        ],
    )
    return attachment_response(workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "temperature_readings.xlsx")


@app.get("/api/export/device-template.xlsx")
def export_device_template() -> Response:
    workbook = make_workbook(
        "设备导入模板",
        ["device_code", "device_name", "workshop", "location", "status", "target_temperature", "safe_min", "safe_max", "owner"],
        [["TCU-NEW-001", "新温控设备", "A车间", "热处理线", "running", 68, 55, 82, "设备管理员"]],
    )
    return attachment_response(workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "temperature_device_template.xlsx")


@app.get("/api/export/points.xlsx")
def export_points_excel() -> Response:
    rows = fetch_all(
        """
        SELECT d.device_code, p.point_code, p.point_name, p.parameter_name,
               p.unit, p.upper_limit, p.lower_limit, p.created_at
        FROM temperature_points p
        JOIN temperature_devices d ON d.id = p.device_id
        WHERE p.deleted_at IS NULL
        ORDER BY p.id ASC
        """
    )
    workbook = make_workbook(
        "temperature_points",
        ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit", "created_at"],
        [
            [
                row["device_code"],
                row["point_code"],
                row["point_name"],
                row["parameter_name"],
                row["unit"],
                row["upper_limit"],
                row["lower_limit"],
                row["created_at"],
            ]
            for row in rows
        ],
    )
    return attachment_response(workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "temperature_points.xlsx")


@app.get("/api/export/point-template.xlsx")
def export_point_template() -> Response:
    workbook = make_workbook(
        "point_template",
        ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"],
        [["TCU-A-001", "TP-NEW-001", "outlet temperature point", "temperature", "C", 90, 40]],
    )
    return attachment_response(workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "temperature_point_template.xlsx")


@app.get("/api/export/analysis-report.csv")
def export_analysis_report() -> Response:
    summary = analytics_summary()
    output = StringIO()
    output.write("section,name,value\n")
    for row in summary["request_status"]:
        output.write(f"审批状态,{row['status']},{row['total']}\n")
    for row in summary["alarm_severity"]:
        output.write(f"异常等级,{row['severity']},{row['total']}\n")
    for row in summary["parameters"]:
        output.write(f"参数变更,{row['parameter_name']},{row['total']}\n")
    return attachment_response(output.getvalue(), "text/csv; charset=utf-8", "temperature_analysis_report.csv")


@app.post("/api/import/devices.xlsx")
async def import_devices_excel(file: UploadFile = File(...)) -> dict:
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 xlsx 文件")
    content = await file.read()
    workbook = Workbook()
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Excel 文件无法读取") from exc
    sheet = workbook.active
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    required = ["device_code", "device_name", "workshop", "location", "status", "target_temperature", "safe_min", "safe_max", "owner"]
    missing = [item for item in required if item not in headers]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少字段: {', '.join(missing)}")
    index = {name: headers.index(name) for name in required}
    imported = 0
    with closing(get_connection()) as connection:
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or not row[index["device_code"]]:
                continue
            try:
                values = {
                    name: row[position]
                    for name, position in index.items()
                }
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO temperature_devices
                    (device_code, device_name, workshop, location, status, target_temperature, safe_min, safe_max, owner)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(values["device_code"]),
                        str(values["device_name"]),
                        str(values["workshop"]),
                        str(values["location"]),
                        str(values["status"] or "running"),
                        float(values["target_temperature"]),
                        float(values["safe_min"]),
                        float(values["safe_max"]),
                        str(values["owner"]),
                    ),
                )
                imported += max(cursor.rowcount, 0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="字段校验失败：温度字段必须是数字") from exc
        connection.commit()
    return {"message": "导入完成", "imported": imported}


@app.post("/api/import/points.xlsx")
async def import_points_excel(file: UploadFile = File(...)) -> dict:
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 xlsx 文件")
    try:
        workbook = load_workbook(BytesIO(await file.read()))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Excel 文件无法读取") from exc
    sheet = workbook.active
    headers = [str(cell.value or "").strip() for cell in sheet[1]]
    required = ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"]
    missing = [item for item in required if item not in headers]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少字段: {', '.join(missing)}")
    index = {name: headers.index(name) for name in required}
    imported = 0
    with closing(get_connection()) as connection:
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not row or not row[index["device_code"]] or not row[index["point_code"]]:
                continue
            device = connection.execute(
                "SELECT id FROM temperature_devices WHERE device_code = ? AND deleted_at IS NULL",
                (str(row[index["device_code"]]),),
            ).fetchone()
            if device is None:
                raise HTTPException(status_code=400, detail=f"设备编号不存在: {row[index['device_code']]}")
            try:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO temperature_points
                    (device_id, point_code, point_name, parameter_name, unit, upper_limit, lower_limit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device["id"],
                        str(row[index["point_code"]]),
                        str(row[index["point_name"]]),
                        str(row[index["parameter_name"]] or "temperature"),
                        str(row[index["unit"]] or "C"),
                        float(row[index["upper_limit"]]),
                        float(row[index["lower_limit"]]),
                    ),
                )
                imported += max(cursor.rowcount, 0)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="字段校验失败：上下限必须是数字") from exc
        connection.commit()
    return {"message": "导入完成", "imported": imported}


@app.get("/")
def frontend_root() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "login.html")


if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
