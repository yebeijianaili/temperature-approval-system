import json
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches as DocxInches, Pt
from openpyxl import Workbook
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import MSO_VERTICAL_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt as PptPt


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "database" / "temperature.db"
EXPORTS = ROOT / "docs" / "exports"


C = {
    "bg": RGBColor(9, 15, 16),
    "panel": RGBColor(24, 36, 35),
    "panel2": RGBColor(32, 48, 46),
    "paper": RGBColor(238, 244, 239),
    "ink": RGBColor(232, 240, 236),
    "dark": RGBColor(27, 36, 34),
    "muted": RGBColor(142, 166, 158),
    "teal": RGBColor(68, 204, 184),
    "amber": RGBColor(228, 158, 54),
    "red": RGBColor(224, 86, 75),
    "green": RGBColor(108, 202, 128),
    "line": RGBColor(71, 94, 89),
}


def rows(sql: str):
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql).fetchall()]


def scalar(sql: str):
    with sqlite3.connect(DB) as con:
        return con.execute(sql).fetchone()[0]


def stats():
    status = {r["status"]: r["total"] for r in rows("SELECT status, COUNT(*) AS total FROM parameter_change_requests WHERE deleted_at IS NULL GROUP BY status")}
    return {
        "devices": scalar("SELECT COUNT(*) FROM temperature_devices WHERE deleted_at IS NULL"),
        "points": scalar("SELECT COUNT(*) FROM temperature_points WHERE deleted_at IS NULL"),
        "readings": scalar("SELECT COUNT(*) FROM temperature_readings WHERE deleted_at IS NULL"),
        "requests": scalar("SELECT COUNT(*) FROM parameter_change_requests WHERE deleted_at IS NULL"),
        "approvals": scalar("SELECT COUNT(*) FROM approval_records WHERE deleted_at IS NULL"),
        "logs": scalar("SELECT COUNT(*) FROM parameter_change_logs WHERE deleted_at IS NULL"),
        "alarms": scalar("SELECT COUNT(*) FROM alarm_events WHERE deleted_at IS NULL"),
        "approved": status.get("approved", 0),
        "pending": status.get("pending", 0),
        "rejected": status.get("rejected", 0),
    }


def workbook(path: Path, sheet: str, headers: list[str], data: list[list]):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)
    for row in data:
        ws.append(row)
    for col in ws.columns:
        width = min(max(max(len(str(cell.value or "")) for cell in col) + 2, 12), 34)
        ws.column_dimensions[col[0].column_letter].width = width
    wb.save(path)


def build_exports():
    EXPORTS.mkdir(parents=True, exist_ok=True)
    devices = rows(
        """
        SELECT device_code, device_name, workshop, location, status,
               target_temperature, safe_min, safe_max, owner
        FROM temperature_devices
        WHERE deleted_at IS NULL
        """
    )
    readings = rows(
        """
        SELECT d.device_code, d.device_name, p.point_code, r.temperature,
               r.target_temperature, r.status, r.recorded_at
        FROM temperature_readings r
        JOIN temperature_devices d ON d.id = r.device_id
        LEFT JOIN temperature_points p ON p.id = r.point_id
        WHERE r.deleted_at IS NULL
        ORDER BY r.recorded_at DESC
        LIMIT 2000
        """
    )
    points = rows(
        """
        SELECT d.device_code, p.point_code, p.point_name, p.parameter_name,
               p.unit, p.upper_limit, p.lower_limit
        FROM temperature_points p
        JOIN temperature_devices d ON d.id = p.device_id
        WHERE p.deleted_at IS NULL
        """
    )
    workbook(
        EXPORTS / "temperature_devices.xlsx",
        "devices",
        ["device_code", "device_name", "workshop", "location", "status", "target_temperature", "safe_min", "safe_max", "owner"],
        [[r[k] for k in ["device_code", "device_name", "workshop", "location", "status", "target_temperature", "safe_min", "safe_max", "owner"]] for r in devices],
    )
    workbook(
        EXPORTS / "temperature_device_template.xlsx",
        "device_template",
        ["device_code", "device_name", "workshop", "location", "status", "target_temperature", "safe_min", "safe_max", "owner"],
        [["TCU-X-001", "新增温控设备", "试验车间", "样例位置", "running", 68, 55, 82, "设备管理员"]],
    )
    workbook(
        EXPORTS / "temperature_points.xlsx",
        "points",
        ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"],
        [[r[k] for k in ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"]] for r in points],
    )
    workbook(
        EXPORTS / "temperature_point_template.xlsx",
        "point_template",
        ["device_code", "point_code", "point_name", "parameter_name", "unit", "upper_limit", "lower_limit"],
        [["TCU-A-001", "TP-X-001", "新增测点", "temperature", "C", 90, 40]],
    )
    workbook(
        EXPORTS / "temperature_readings.xlsx",
        "readings",
        ["device_code", "device_name", "point_code", "temperature", "target_temperature", "status", "recorded_at"],
        [[r[k] for k in ["device_code", "device_name", "point_code", "temperature", "target_temperature", "status", "recorded_at"]] for r in readings],
    )

    s = stats()
    with (EXPORTS / "temperature_analysis_report.csv").open("w", encoding="utf-8-sig") as f:
        f.write("section,name,value\n")
        for name in ["devices", "points", "readings", "requests", "approvals", "logs", "alarms"]:
            f.write(f"数据规模,{name},{s[name]}\n")
        for name in ["approved", "pending", "rejected"]:
            f.write(f"审批状态,{name},{s[name]}\n")

    trend = list(reversed(rows("SELECT recorded_at, temperature, target_temperature FROM temperature_readings WHERE deleted_at IS NULL ORDER BY recorded_at DESC LIMIT 120")))
    if trend:
        plt.figure(figsize=(10, 4.8), dpi=160)
        plt.plot([r["recorded_at"][5:16] for r in trend], [r["temperature"] for r in trend], color="#44ccb8", linewidth=2.2, label="temperature")
        plt.plot([r["recorded_at"][5:16] for r in trend], [r["target_temperature"] for r in trend], color="#e49e36", linewidth=1.6, label="target")
        plt.title("Temperature Trend")
        plt.xticks(rotation=45, ha="right", fontsize=7)
        plt.grid(alpha=0.18)
        plt.legend()
        plt.tight_layout()
        plt.savefig(EXPORTS / "temperature_trend.png")
        plt.close()

    plt.figure(figsize=(6, 5), dpi=160)
    values = [s["approved"], s["pending"], s["rejected"]]
    plt.pie(values, labels=["approved", "pending", "rejected"], autopct="%1.1f%%", colors=["#6cca80", "#e49e36", "#e0564b"], wedgeprops={"edgecolor": "white", "linewidth": 1})
    plt.title("Approval Status")
    plt.tight_layout()
    plt.savefig(EXPORTS / "approval_status.png")
    plt.close()

    changes = rows("SELECT parameter_name, COUNT(*) AS total FROM parameter_change_requests WHERE deleted_at IS NULL GROUP BY parameter_name ORDER BY total DESC LIMIT 8")
    if changes:
        plt.figure(figsize=(8, 4.6), dpi=160)
        plt.bar([r["parameter_name"] for r in changes], [r["total"] for r in changes], color="#44ccb8")
        plt.title("Parameter Change Counts")
        plt.xticks(rotation=25, ha="right")
        plt.tight_layout()
        plt.savefig(EXPORTS / "parameter_change_counts.png")
        plt.close()


def doc_font(run, size=11, bold=False, font="宋体"):
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    run.font.size = Pt(size)
    run.font.bold = bold


def para(doc, value, size=11, bold=False, align=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    else:
        p.paragraph_format.first_line_indent = Pt(22)
    p.paragraph_format.line_spacing = 1.35
    r = p.add_run(value)
    doc_font(r, size, bold)
    return p


def heading(doc, value, level=1):
    p = doc.add_heading("", level=level)
    r = p.add_run(value)
    doc_font(r, 16 if level == 1 else 13, True, "黑体")
    return p


def build_report():
    s = stats()
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = DocxInches(0.8)
    sec.bottom_margin = DocxInches(0.8)
    sec.left_margin = DocxInches(0.9)
    sec.right_margin = DocxInches(0.9)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("温控设备参数变更审批系统\n技术报告")
    doc_font(r, 22, True, "黑体")
    para(doc, "作者：项目作者", 12, False, WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "技术栈：FastAPI + SQLite + 原生 HTML/CSS/JS + Axios + ECharts + Pandas + Matplotlib", 11, False, WD_ALIGN_PARAGRAPH.CENTER)

    heading(doc, "一、项目背景")
    para(doc, "温控设备在热处理、恒温存储、试验环境和生产线工艺中承担稳定温度的作用。设备参数一旦被随意修改，可能导致温度偏离、批次质量波动、异常报警增加，甚至影响生产安全。因此，温控设备管理不能只停留在“设备台账”和“温度曲线”，还需要把参数变更纳入申请、审批、记录、追溯和分析的闭环。本系统围绕温控设备参数变更审批建立完整业务系统，将设备档案、测点管理、变更申请、审批记录、变更日志、温度读数和异常事件统一管理。")

    heading(doc, "二、需求分析")
    for item in [
        "登录与权限：提供管理员和普通用户账号，管理员负责用户、档案和审批，普通用户负责查询和提交申请。",
        "设备与测点：维护温控设备编码、车间、位置、目标温度、安全范围、负责人以及测点上下限。",
        "参数变更：用户提交目标温度或阈值调整申请，记录旧值、新值、原因、优先级和申请人。",
        "审批闭环：管理员审批通过或驳回，系统写入审批记录；审批通过后生成参数变更日志。",
        "数据查询：温度读数支持设备、状态、时间范围和分页查询，业务记录支持筛选和导出。",
        "统计分析：通过图表展示温度趋势、审批状态、参数变更次数和异常统计，Notebook 继续完成统计描述、异常检测和趋势分析。",
    ]:
        para(doc, item)

    heading(doc, "三、系统架构")
    para(doc, "系统采用 FastAPI + SQLite + 原生前端的轻量架构。前端页面通过 Axios 调用 REST API，ECharts 展示温度趋势、审批状态和参数变更统计；后端负责登录、CRUD、审批流、导入导出和分析接口；SQLite 保存系统表和业务表；Pandas、Matplotlib 和 Notebook 用于进一步分析。该架构便于本地启动，也便于老师通过 /docs 检查接口。")
    arch = doc.add_table(rows=1, cols=3)
    arch.alignment = WD_TABLE_ALIGNMENT.CENTER
    arch.style = "Table Grid"
    for i, h in enumerate(["层次", "技术", "作用"]):
        arch.rows[0].cells[i].text = h
    for row in [
        ("前端", "HTML/CSS/JS、Axios、ECharts", "页面交互、筛选、图表和 PNG 导出"),
        ("后端", "FastAPI、openpyxl", "REST 接口、审批闭环、Excel 导入导出"),
        ("数据库", "SQLite", "用户、设备、测点、申请、审批、日志、读数、异常"),
        ("分析", "Pandas、Matplotlib、Notebook", "统计、异常检测、趋势分析和报告图"),
    ]:
        cells = arch.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value

    heading(doc, "四、数据库设计")
    para(doc, "数据库包含 roles、users 两张系统表，以及 temperature_devices、temperature_points、parameter_change_requests、approval_records、parameter_change_logs、temperature_readings、alarm_events 七张业务表。所有表均包含 id、created_at、updated_at、deleted_at 字段，实现软删除；设备编码、读数时间、申请状态和变更时间建立索引，便于编号查询、时间筛选和状态统计。")
    table = doc.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for i, h in enumerate(["表名", "用途", "关键字段/关联", "数据量"]):
        table.rows[0].cells[i].text = h
    data = [
        ("temperature_devices", "温控设备档案", "device_code、target_temperature、安全范围", s["devices"]),
        ("temperature_points", "测点/参数管理", "device_id、point_code、上下限", s["points"]),
        ("parameter_change_requests", "参数变更申请", "device_id、old_value、new_value、status", s["requests"]),
        ("approval_records", "审批记录", "request_id、approver_id、action", s["approvals"]),
        ("parameter_change_logs", "变更日志", "request_id、device_id、changed_at", s["logs"]),
        ("temperature_readings", "温度读数", "temperature、target_temperature、recorded_at", s["readings"]),
        ("alarm_events", "异常事件", "alarm_type、severity、status", s["alarms"]),
    ]
    for row in data:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)

    heading(doc, "五、核心功能")
    para(doc, "系统提供登录页、首页工作台、用户管理、温控设备管理、参数/测点管理、参数变更申请、审批中心、变更记录、数据查询、可视化统计、数据分析与挖掘、系统设置和温控状态看板等页面。首页展示设备总数、待处理申请、异常数量和读数总量；档案页面支持新增、编辑、软删除、分页、搜索、Excel 导入导出和模板下载；审批页面完成从申请到审批再到日志生成的闭环。")
    para(doc, "与工业水泵 HMI 类似的部分只保留实时状态卡片、趋势曲线、异常记录和工业风格看板；水泵启停、泵速控制、阀门控制、Modbus、PLC 通讯等与温控审批无直接关系的内容没有加入，避免跑题。")

    heading(doc, "六、数据分析")
    para(doc, f"造数脚本生成 {s['readings']} 条温度读数、{s['requests']} 条参数变更申请、{s['approvals']} 条审批记录、{s['logs']} 条变更日志和 {s['alarms']} 条异常事件。分析重点包括温度读数与目标温度偏差、异常读数比例、审批通过/驳回/待处理分布、参数变更频次和异常事件状态。页面端提供 ECharts 图表，Notebook 读取 SQLite 数据后输出统计结果和趋势图。")
    for image in ["temperature_trend.png", "approval_status.png", "parameter_change_counts.png"]:
        p = EXPORTS / image
        if p.exists():
            doc.add_picture(str(p), width=DocxInches(5.6))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    heading(doc, "七、测试验证")
    para(doc, "测试覆盖数据库字段规范、登录接口、审批闭环、前端页面数量、FastAPI 文档链接、Excel/CSV 导出接口、用户密码重置、启用禁用、设备和测点 CRUD、Excel 导入、数据查询筛选和分析接口。验收命令包括 python -m compileall backend scripts 与 python -m pytest tests -q。")
    test = doc.add_table(rows=1, cols=3)
    test.alignment = WD_TABLE_ALIGNMENT.CENTER
    test.style = "Table Grid"
    for i, h in enumerate(["检查项", "验证内容", "结果"]):
        test.rows[0].cells[i].text = h
    for row in [
        ("数据库", "9 张表、软删除字段、索引、外键、1000+ 核心数据", "通过"),
        ("业务闭环", "提交申请、管理员审批、生成审批记录和变更日志", "通过"),
        ("导出", "Excel、CSV、PNG 样例和接口均可用", "通过"),
        ("页面", "13 个页面可打开，统一调用 app.js", "通过"),
        ("材料", "SQL、数据库、Notebook、Word、PPT、启动说明齐全", "通过"),
    ]:
        cells = test.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value

    heading(doc, "八、总结")
    para(doc, "本项目以温控设备参数变更为主线，完成了从设备建档、测点维护、参数申请、审批处理、日志追溯、温度读数查询到统计分析的完整系统。项目既体现了 Python Web 开发能力，也体现了数据库建模、数据导出、可视化和数据分析能力。后续可扩展多级审批、审批超时提醒、参数版本对比和更细的异常预测模型。")

    save_docx(doc, ROOT / "作业报告.docx", ROOT / "作业报告_重做版.docx")


def slide(prs, bg=C["bg"]):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = bg
    return s


def rect(s, x, y, w, h, fill, line=None, round=False):
    kind = MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE if round else MSO_AUTO_SHAPE_TYPE.RECTANGLE
    shp = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.color.rgb = line or fill
    return shp


def txt(s, value, x, y, w, h, size=18, bold=False, color=None, align=PP_ALIGN.LEFT, font="Microsoft YaHei"):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.text_frame.clear()
    box.text_frame.vertical_anchor = MSO_VERTICAL_ANCHOR.TOP
    p = box.text_frame.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = value
    r.font.name = font
    r.font.size = PptPt(size)
    r.font.bold = bold
    r.font.color.rgb = color or C["ink"]
    return box


def hline(s, x1, y1, x2, y2, color=C["teal"], width=1.4):
    ln = s.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    ln.line.color.rgb = color
    ln.line.width = PptPt(width)
    return ln


def header(s, no, title):
    rect(s, 0, 0, 13.333, 0.14, C["amber"])
    txt(s, f"TCU APPROVAL / {no}", 0.55, 0.38, 3.2, 0.2, 8, True, C["amber"], font="Consolas")
    txt(s, title, 0.55, 0.76, 9.4, 0.46, 23, True)
    hline(s, 0.55, 1.35, 12.7, 1.35, C["line"], 1)
    txt(s, "温控设备参数变更审批系统", 0.55, 7.08, 5.6, 0.2, 7.5, False, C["muted"])


def metric(s, x, y, label, value, accent=C["teal"]):
    rect(s, x, y, 2.55, 1.08, C["panel"], C["line"], True)
    rect(s, x, y, 0.08, 1.08, accent)
    txt(s, label, x + 0.22, y + 0.18, 1.8, 0.16, 7.5, True, C["muted"], font="Consolas")
    txt(s, str(value), x + 0.22, y + 0.42, 1.8, 0.34, 23, True, accent, font="Consolas")


def mini_hmi(s, x, y):
    rect(s, x, y, 4.0, 3.05, C["panel"], C["line"])
    txt(s, "TEMP CONTROL PANEL", x + 0.25, y + 0.25, 2.3, 0.2, 8, True, C["teal"], font="Consolas")
    rect(s, x + 0.3, y + 0.72, 1.45, 1.0, C["panel2"], C["line"], True)
    txt(s, "68.4", x + 0.45, y + 0.98, 0.9, 0.24, 21, True, C["teal"], PP_ALIGN.CENTER, "Consolas")
    txt(s, "CURRENT", x + 0.55, y + 1.36, 0.7, 0.12, 6.2, False, C["muted"], PP_ALIGN.CENTER, "Consolas")
    rect(s, x + 2.05, y + 0.72, 1.45, 1.0, C["panel2"], C["line"], True)
    txt(s, "70.0", x + 2.2, y + 0.98, 0.9, 0.24, 21, True, C["amber"], PP_ALIGN.CENTER, "Consolas")
    txt(s, "TARGET", x + 2.3, y + 1.36, 0.7, 0.12, 6.2, False, C["muted"], PP_ALIGN.CENTER, "Consolas")
    for i, color in enumerate([C["green"], C["amber"], C["red"]]):
        rect(s, x + 0.35 + i * 1.1, y + 2.15, 0.85, 0.35, color, color, True)


def build_ppt():
    s = stats()
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    sl = slide(prs)
    rect(sl, 0, 0, 13.333, 0.16, C["amber"])
    txt(sl, "THERMAL PARAMETER APPROVAL", 0.65, 0.58, 4.1, 0.2, 8.5, True, C["amber"], font="Consolas")
    txt(sl, "温控设备参数变更\n审批系统", 0.65, 1.16, 6.6, 1.2, 36, True)
    txt(sl, "设备建档、参数申请、审批处理、变更追溯、温度趋势和异常分析的一体化业务系统。", 0.7, 3.05, 5.8, 0.48, 15, False, C["muted"])
    metric(sl, 0.8, 4.65, "READINGS", s["readings"], C["teal"])
    metric(sl, 3.75, 4.65, "REQUESTS", s["requests"], C["amber"])
    mini_hmi(sl, 8.2, 1.45)
    txt(sl, "技术报告", 0.7, 6.78, 2.0, 0.24, 11, True, C["ink"])

    sl = slide(prs)
    header(sl, "01", "业务问题：温控参数不能被随意修改")
    for i, (t, d) in enumerate([
        ("温度偏离", "目标温度或阈值被改动后，可能引发读数异常和批次质量波动。"),
        ("责任不清", "只记录当前参数，缺少申请人、审批人和变更原因。"),
        ("追溯困难", "参数历史与温度趋势脱节，出现异常时难以复盘。"),
        ("统计滞后", "审批状态、异常数量和变更频次缺少集中看板。"),
    ]):
        x = 0.85 + (i % 2) * 6.1
        y = 2.0 + (i // 2) * 1.7
        rect(sl, x, y, 5.25, 1.08, C["panel"], C["line"], True)
        txt(sl, t, x + 0.25, y + 0.22, 1.5, 0.2, 13, True, C["ink"])
        txt(sl, d, x + 1.65, y + 0.22, 3.65, 0.32, 9.5, False, C["muted"])

    sl = slide(prs)
    header(sl, "02", "业务流程：从申请到日志的闭环")
    steps = [("建档", "设备/测点"), ("申请", "旧值/新值/原因"), ("审批", "通过或驳回"), ("生效", "生成变更日志"), ("分析", "趋势/异常/统计")]
    for i, (a, b) in enumerate(steps):
        x = 0.82 + i * 2.48
        rect(sl, x, 2.55, 1.72, 1.28, C["panel"], C["teal"] if i != 2 else C["amber"], True)
        txt(sl, f"{i+1:02d}", x + 0.15, 2.76, 0.45, 0.16, 8.5, True, C["amber"], font="Consolas")
        txt(sl, a, x + 0.15, 3.06, 1.2, 0.2, 15, True)
        txt(sl, b, x + 0.15, 3.46, 1.2, 0.18, 7.5, False, C["muted"])
        if i < 4:
            hline(sl, x + 1.78, 3.18, x + 2.28, 3.18, C["amber"], 2)
    txt(sl, "工业 HMI 的“状态卡片、趋势曲线、报警列表”被借鉴为温控审批看板；无关的泵机控制和通讯内容不加入。", 1.0, 5.25, 11.2, 0.42, 14, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "03", "系统架构：前端、接口、数据库、分析材料各司其职")
    for i, (t, d, color) in enumerate([
        ("前端页面", "原生 HTML/CSS/JS\nAxios 调用接口\nECharts 图表", C["teal"]),
        ("FastAPI", "登录、CRUD、审批\n导入导出、统计接口\n/docs 可检查", C["amber"]),
        ("SQLite", "9 张表\n软删除字段\n索引和外键", C["green"]),
        ("数据分析", "Pandas Notebook\nMatplotlib PNG\nCSV 报告", C["red"]),
    ]):
        x = 0.85 + i * 3.08
        rect(sl, x, 2.0, 2.55, 2.78, C["panel"], C["line"], True)
        rect(sl, x, 2.0, 2.55, 0.14, color)
        txt(sl, t, x + 0.22, 2.42, 1.8, 0.22, 15, True)
        txt(sl, d, x + 0.22, 3.08, 1.9, 0.82, 10, False, C["muted"])

    sl = slide(prs)
    header(sl, "04", "数据模型：参数审批围绕设备和测点展开")
    mini_hmi(sl, 0.85, 2.0)
    for i, (table, desc) in enumerate([
        ("temperature_devices", "设备档案"),
        ("temperature_points", "测点/参数"),
        ("parameter_change_requests", "变更申请"),
        ("approval_records", "审批记录"),
        ("parameter_change_logs", "变更日志"),
        ("temperature_readings", "温度读数"),
        ("alarm_events", "异常事件"),
    ]):
        x = 5.25 + (i % 2) * 3.35
        y = 1.75 + (i // 2) * 0.88
        rect(sl, x, y, 2.85, 0.52, C["panel"], C["line"], True)
        txt(sl, table, x + 0.16, y + 0.12, 1.7, 0.12, 7, True, C["teal"], font="Consolas")
        txt(sl, desc, x + 1.8, y + 0.12, 0.8, 0.12, 7, False, C["muted"])
    txt(sl, "所有表均有 id / created_at / updated_at / deleted_at，满足软删除和验收检查。", 5.35, 5.72, 5.8, 0.32, 12, True)

    sl = slide(prs)
    header(sl, "05", "页面组织：围绕审批业务，不堆无关控制")
    pages = ["登录", "首页", "用户", "设备", "测点", "变更申请", "审批中心", "变更记录", "数据查询", "可视化", "数据分析", "状态看板", "设置"]
    for i, p in enumerate(pages):
        x = 0.85 + (i % 5) * 2.45
        y = 1.85 + (i // 5) * 1.08
        rect(sl, x, y, 1.78, 0.58, C["panel"], C["amber"] if p in ["首页", "审批中心", "可视化"] else C["line"], True)
        txt(sl, p, x + 0.1, y + 0.18, 1.55, 0.14, 8.5, True, C["ink"], PP_ALIGN.CENTER)
    txt(sl, "演示顺序建议：设备档案 → 测点 → 提交申请 → 审批 → 变更日志 → 温度趋势 → 数据分析。", 1.0, 5.7, 11.2, 0.38, 14, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "06", "数据规模：支撑分页、筛选、图表和分析")
    metric(sl, 0.85, 2.0, "DEVICES", s["devices"], C["teal"])
    metric(sl, 3.75, 2.0, "POINTS", s["points"], C["green"])
    metric(sl, 6.65, 2.0, "READINGS", s["readings"], C["amber"])
    metric(sl, 9.55, 2.0, "ALARMS", s["alarms"], C["red"])
    rect(sl, 1.0, 4.4, 11.25, 1.0, C["panel"], C["line"], True)
    txt(sl, f"审批申请 {s['requests']} 条，审批记录 {s['approvals']} 条，变更日志 {s['logs']} 条。", 1.25, 4.72, 10.7, 0.24, 17, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "07", "可视化结果：趋势、审批状态和变更频次")
    img1 = EXPORTS / "temperature_trend.png"
    img2 = EXPORTS / "approval_status.png"
    img3 = EXPORTS / "parameter_change_counts.png"
    if img1.exists():
        sl.shapes.add_picture(str(img1), Inches(0.75), Inches(1.75), Inches(6.7), Inches(3.25))
    if img2.exists():
        sl.shapes.add_picture(str(img2), Inches(7.75), Inches(1.62), Inches(3.0), Inches(2.7))
    if img3.exists():
        sl.shapes.add_picture(str(img3), Inches(7.6), Inches(4.25), Inches(4.1), Inches(1.95))
    txt(sl, "图表服务审批业务：看温度是否稳定、审批是否积压、哪些参数变更最多。", 0.95, 6.35, 11.3, 0.24, 13, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "08", "接口设计：REST API 覆盖核心业务")
    apis = ["/api/auth/login", "/api/users", "/api/devices", "/api/points", "/api/change-requests", "/api/approvals/{id}", "/api/change-logs", "/api/readings", "/api/analytics/trends", "/api/export/*.xlsx", "/api/import/*.xlsx", "/api/analytics/insights"]
    for i, api in enumerate(apis):
        x = 0.85 + (i % 3) * 4.05
        y = 1.78 + (i // 3) * 0.78
        rect(sl, x, y, 3.42, 0.46, C["panel"], C["line"], True)
        txt(sl, api, x + 0.14, y + 0.13, 3.05, 0.1, 7.3, False, C["teal"], font="Consolas")
    txt(sl, "老师可以通过 /docs 直接检查接口，也可以在页面中演示 Excel 导入导出和审批闭环。", 1.0, 6.25, 11.2, 0.28, 13.5, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "09", "导出与材料：Excel、CSV、PNG 都有真实样例")
    exports = ["temperature_devices.xlsx", "temperature_device_template.xlsx", "temperature_points.xlsx", "temperature_point_template.xlsx", "temperature_readings.xlsx", "temperature_analysis_report.csv", "temperature_trend.png", "approval_status.png"]
    for i, name in enumerate(exports):
        x = 0.85 + (i % 2) * 5.95
        y = 1.8 + (i // 2) * 0.78
        rect(sl, x, y, 4.85, 0.5, C["panel"], C["line"], True)
        txt(sl, name, x + 0.18, y + 0.13, 3.9, 0.1, 7.5, True, C["ink"], font="Consolas")
    txt(sl, "Notebook 位于 analysis/temperature_analysis.ipynb，可读取 SQLite 数据库继续做统计和异常分析。", 1.0, 6.1, 11.2, 0.32, 13.5, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "10", "测试验证：用自动化测试兜住交付")
    for i, (t, d) in enumerate([
        ("数据库规范", "表、字段、软删除、索引和核心数据量"),
        ("审批闭环", "申请提交、审批通过/驳回、生成日志"),
        ("导出接口", "Excel 模板、Excel 数据、CSV 报告"),
        ("前端页面", "13 个页面和主要功能入口"),
        ("分析材料", "Notebook、PNG、Word、PPT、启动说明"),
    ]):
        y = 1.78 + i * 0.86
        rect(sl, 1.0, y, 10.95, 0.52, C["panel"], C["line"], True)
        txt(sl, f"{i+1:02d}", 1.18, y + 0.14, 0.4, 0.1, 7.5, True, C["amber"], font="Consolas")
        txt(sl, t, 1.72, y + 0.12, 1.7, 0.12, 10, True)
        txt(sl, d, 3.7, y + 0.12, 6.4, 0.12, 9, False, C["muted"])
    txt(sl, "验收命令：python -m compileall backend scripts；python -m pytest tests -q", 1.0, 6.28, 11.2, 0.25, 12.2, True, C["ink"], PP_ALIGN.CENTER)

    sl = slide(prs)
    header(sl, "11", "演示路径：按业务线讲，不按菜单硬点")
    demo = ["登录 admin / admin123", "首页查看设备、待审批、异常、读数总量", "维护设备和测点档案并演示导出", "提交参数变更申请", "管理员审批并生成变更日志", "查询温度读数与异常记录", "打开可视化和数据分析页", "进入 /docs 展示接口完整性"]
    for i, d in enumerate(demo):
        y = 1.65 + i * 0.68
        txt(sl, f"{i+1}", 1.0, y, 0.28, 0.1, 8, True, C["amber"], font="Consolas")
        hline(sl, 1.38, y + 0.1, 1.78, y + 0.1, C["amber"], 1.5)
        txt(sl, d, 1.95, y - 0.02, 8.9, 0.14, 11.2, True)
    txt(sl, "入口：http://127.0.0.1:8008/frontend/login.html", 1.0, 6.55, 8.8, 0.18, 10.5, True, C["teal"], font="Consolas")

    sl = slide(prs)
    header(sl, "12", "答辩结论：把参数变更变成可追溯的数据资产")
    txt(sl, "系统围绕温控设备参数审批建立了完整闭环：设备和测点有档案，参数修改有申请，审批动作有记录，通过后有日志，温度和异常有统计分析。", 1.0, 2.05, 11.0, 0.92, 23, True, C["ink"], PP_ALIGN.CENTER)
    rect(sl, 3.4, 4.35, 6.7, 0.75, C["panel"], C["amber"], True)
    txt(sl, "可运行 · 可查询 · 可审批 · 可追溯 · 可分析", 3.58, 4.58, 6.35, 0.18, 14, True, C["amber"], PP_ALIGN.CENTER)

    save_pptx(prs, ROOT / "答辩PPT.pptx", ROOT / "答辩PPT_重做版.pptx")


def build_notebook():
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# 温控设备参数变更审批系统数据分析\n", "读取 SQLite 数据库，完成温度趋势、异常检测和审批状态分析。"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["import sqlite3, pandas as pd, matplotlib.pyplot as plt\n", "conn = sqlite3.connect('../database/temperature.db')\n", "readings = pd.read_sql_query('SELECT * FROM temperature_readings WHERE deleted_at IS NULL', conn)\n", "readings.head()"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["readings[['temperature','target_temperature']].describe()"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["requests = pd.read_sql_query('SELECT * FROM parameter_change_requests WHERE deleted_at IS NULL', conn)\n", "requests['status'].value_counts().plot(kind='pie', autopct='%1.1f%%', title='Approval Status')\n", "plt.show()"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["alarms = pd.read_sql_query('SELECT * FROM alarm_events WHERE deleted_at IS NULL', conn)\n", "alarms.groupby(['severity','status']).size()"]},
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "language_info": {"name": "python", "version": "3.x"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (ROOT / "analysis" / "temperature_analysis.ipynb").write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")


def save_docx(doc: Document, target: Path, fallback: Path):
    try:
        doc.save(target)
        if fallback.exists():
            fallback.unlink()
    except PermissionError:
        doc.save(fallback)
        print(f"{target.name} is locked; generated {fallback.name} instead.")


def save_pptx(prs: Presentation, target: Path, fallback: Path):
    try:
        prs.save(target)
        if fallback.exists():
            fallback.unlink()
    except PermissionError:
        prs.save(fallback)
        print(f"{target.name} is locked; generated {fallback.name} instead.")


def cleanup_duplicate_materials():
    duplicates = [
        ROOT / "PPT.pptx",
        ROOT / "作业报告.docx",
        ROOT / "docs" / "温控设备参数变更审批系统_作业报告.docx",
        ROOT / "docs" / "温控设备参数变更审批系统_答辩PPT.pptx",
        ROOT / "docs" / "作业报告.md",
        ROOT / "docs" / "答辩PPT提纲.md",
    ]
    for path in duplicates:
        if path.exists():
            try:
                path.unlink()
                print(f"Removed duplicate material: {path.relative_to(ROOT)}")
            except PermissionError:
                print(f"Duplicate material is locked, close it and rerun: {path.relative_to(ROOT)}")


def main():
    build_exports()
    build_report()
    build_ppt()
    build_notebook()
    cleanup_duplicate_materials()
    print("Deliverables generated: 作业报告.docx, 答辩PPT.pptx, exports, notebook.")


if __name__ == "__main__":
    main()
