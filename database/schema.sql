PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_code TEXT NOT NULL UNIQUE,
    role_name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    real_name TEXT NOT NULL,
    password TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'enabled',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES roles(id)
);

CREATE TABLE IF NOT EXISTS temperature_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_code TEXT NOT NULL UNIQUE,
    device_name TEXT NOT NULL,
    workshop TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    target_temperature REAL NOT NULL,
    safe_min REAL NOT NULL,
    safe_max REAL NOT NULL,
    owner TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS temperature_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    point_code TEXT NOT NULL UNIQUE,
    point_name TEXT NOT NULL,
    parameter_name TEXT NOT NULL DEFAULT 'temperature',
    unit TEXT NOT NULL DEFAULT 'C',
    upper_limit REAL NOT NULL,
    lower_limit REAL NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES temperature_devices(id)
);

CREATE TABLE IF NOT EXISTS parameter_change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    parameter_name TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    reason TEXT NOT NULL,
    applicant_id INTEGER NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES temperature_devices(id),
    FOREIGN KEY (applicant_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS approval_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    approver_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    comment TEXT,
    approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES parameter_change_requests(id),
    FOREIGN KEY (approver_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS parameter_change_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    parameter_name TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES parameter_change_requests(id),
    FOREIGN KEY (device_id) REFERENCES temperature_devices(id),
    FOREIGN KEY (changed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS temperature_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    point_id INTEGER,
    temperature REAL NOT NULL,
    target_temperature REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'normal',
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES temperature_devices(id),
    FOREIGN KEY (point_id) REFERENCES temperature_points(id)
);

CREATE TABLE IF NOT EXISTS alarm_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    alarm_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    handled_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES temperature_devices(id)
);

CREATE INDEX IF NOT EXISTS idx_devices_code ON temperature_devices(device_code);
CREATE INDEX IF NOT EXISTS idx_readings_recorded_at ON temperature_readings(recorded_at);
CREATE INDEX IF NOT EXISTS idx_requests_status ON parameter_change_requests(status);
CREATE INDEX IF NOT EXISTS idx_logs_changed_at ON parameter_change_logs(changed_at);
