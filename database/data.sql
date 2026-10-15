INSERT OR IGNORE INTO roles (id, role_code, role_name) VALUES
(1, 'admin', '管理员'),
(2, 'operator', '普通用户');

-- 口令一律以 PBKDF2-HMAC-SHA256 加盐哈希存储（algorithm$iterations$salt$digest），
-- 由 backend.main.hash_password 生成；示例口令仅用于本地演示。
INSERT OR IGNORE INTO users (id, username, real_name, password, role_id, status) VALUES
(1, 'admin', '系统管理员', 'pbkdf2_sha256$120000$006da129121fbf1429e589735d2d0600$7851a86e3955ec9af1fb718f847eaef543f3f71866a93a100a719c66df519fbd', 1, 'enabled'),
(2, 'user1', '普通用户', 'pbkdf2_sha256$120000$ec5cd185bcc7064504788f7854896780$cde324e64b262bf93f32184e11aedc587587a75c1fe6565905c3a2837c46270b', 2, 'enabled'),
(3, 'operator', '现场操作员', 'pbkdf2_sha256$120000$ec5cd185bcc7064504788f7854896780$cde324e64b262bf93f32184e11aedc587587a75c1fe6565905c3a2837c46270b', 2, 'enabled');

INSERT OR IGNORE INTO temperature_devices
(id, device_code, device_name, workshop, location, status, target_temperature, safe_min, safe_max, owner)
VALUES
(1, 'TCU-001', '一号温控箱', 'A车间', '热处理一线', 'running', 68.0, 55.0, 82.0, '设备管理员'),
(2, 'TCU-002', '二号温控柜', 'A车间', '热处理二线', 'running', 72.0, 58.0, 86.0, '设备管理员'),
(3, 'TCU-003', '三号恒温槽', 'B车间', '检测区', 'maintenance', 45.0, 38.0, 55.0, '现场操作员');

INSERT OR IGNORE INTO temperature_points
(id, device_id, point_code, point_name, parameter_name, unit, upper_limit, lower_limit)
VALUES
(1, 1, 'TP-001-A', '一号箱入口温度', 'temperature', 'C', 82.0, 55.0),
(2, 1, 'TP-001-B', '一号箱出口温度', 'temperature', 'C', 82.0, 55.0),
(3, 2, 'TP-002-A', '二号柜炉膛温度', 'temperature', 'C', 86.0, 58.0),
(4, 3, 'TP-003-A', '三号槽液温度', 'temperature', 'C', 55.0, 38.0);

INSERT OR IGNORE INTO parameter_change_requests
(id, device_id, parameter_name, old_value, new_value, reason, applicant_id, priority, status)
VALUES
(1, 1, 'target_temperature', '66.0', '68.0', '首批工艺窗口优化', 2, 'normal', 'approved'),
(2, 2, 'safe_max', '84.0', '86.0', '设备换型后安全上限同步', 2, 'high', 'pending'),
(3, 3, 'target_temperature', '48.0', '45.0', '检测区样品低温实验', 3, 'normal', 'rejected');

INSERT OR IGNORE INTO approval_records
(id, request_id, approver_id, action, comment)
VALUES
(1, 1, 1, 'approved', '符合工艺单要求'),
(2, 3, 1, 'rejected', '缺少试验单编号');

INSERT OR IGNORE INTO parameter_change_logs
(id, request_id, device_id, parameter_name, old_value, new_value, changed_by)
VALUES
(1, 1, 1, 'target_temperature', '66.0', '68.0', 1);

INSERT OR IGNORE INTO temperature_readings
(id, device_id, point_id, temperature, target_temperature, status, recorded_at)
VALUES
(1, 1, 1, 67.8, 68.0, 'normal', datetime('now', '-80 minutes')),
(2, 1, 2, 68.4, 68.0, 'normal', datetime('now', '-60 minutes')),
(3, 2, 3, 78.9, 72.0, 'warning', datetime('now', '-40 minutes')),
(4, 3, 4, 45.1, 45.0, 'normal', datetime('now', '-20 minutes'));

INSERT OR IGNORE INTO alarm_events
(id, device_id, alarm_type, severity, message, status, occurred_at)
VALUES
(1, 2, 'temperature_high', 'warning', '二号温控柜温度偏离目标值超过 6C', 'open', datetime('now', '-35 minutes')),
(2, 3, 'approval_missing', 'info', '三号恒温槽变更申请被驳回，需补充试验单', 'closed', datetime('now', '-2 days'));
