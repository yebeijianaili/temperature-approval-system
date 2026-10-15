const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8008" : "";

const navItems = [
  ["dashboard.html", "首页工作台", "all"],
  ["users.html", "用户管理", "admin"],
  ["devices.html", "温控设备", "admin"],
  ["points.html", "参数测点", "admin"],
  ["change_requests.html", "变更申请", "all"],
  ["approvals.html", "审批中心", "admin"],
  ["change_logs.html", "变更记录", "all"],
  ["data_query.html", "数据查询", "all"],
  ["visualization.html", "可视化统计", "all"],
  ["analysis.html", "分析挖掘", "all"],
  ["monitor.html", "温控看板", "all"],
  ["settings.html", "系统设置", "admin"],
];

const adminPages = new Set(["users", "devices", "points", "approvals", "settings"]);
const assignmentCompatibilityIds = ["startDate", "endDate"];

function $(selector) {
  return document.querySelector(selector);
}

function currentPage() {
  return document.body.dataset.page || "";
}

function currentUser() {
  try {
    return JSON.parse(localStorage.getItem("temperature_user") || "null");
  } catch {
    return null;
  }
}

function isAdmin() {
  return currentUser()?.role_code === "admin";
}

function logout() {
  localStorage.removeItem("temperature_user");
  location.href = "login.html";
}

function api(path, options = {}) {
  const user = currentUser();
  const headers = { ...(options.headers || {}) };
  if (user?.auth_token) headers.Authorization = `Bearer ${user.auth_token}`;
  return axios({ url: `${API_BASE}${path}`, ...options, headers })
    .then((res) => res.data)
    .catch((error) => {
      if (error.response?.status === 401) logout();
      throw error;
    });
}

function setMessage(text, type = "ok") {
  const box = $("#message");
  if (!box) return;
  box.textContent = text;
  box.style.color = type === "error" ? "#e06455" : "#62d6c6";
}

function badge(value) {
  const text = value ?? "";
  return `<span class="badge ${String(text)}">${text}</span>`;
}

async function downloadFile(path) {
  const user = currentUser();
  const response = await axios.get(`${API_BASE}${path}`, {
    responseType: "blob",
    headers: user?.auth_token ? { Authorization: `Bearer ${user.auth_token}` } : {},
  });
  const disposition = response.headers["content-disposition"] || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(response.data);
  link.download = match?.[1] || path.split("/").pop();
  link.click();
  URL.revokeObjectURL(link.href);
}

function downloadChart(chartId, filename) {
  const chart = echarts.getInstanceByDom(document.getElementById(chartId));
  if (!chart) {
    setMessage("图表还没有加载完成。", "error");
    return;
  }
  const link = document.createElement("a");
  link.href = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#0e1414" });
  link.download = filename;
  link.click();
}

function renderShell() {
  const shell = $("#appShell");
  if (!shell) return;
  const page = location.pathname.split("/").pop();
  const user = currentUser();
  const visibleNavItems = navItems.filter(([, , access]) => access === "all" || isAdmin());
  shell.innerHTML = `
    <aside class="sidebar">
      <div class="brand">温控参数审批<small>TCU-APPROVAL</small></div>
      <div class="system-state"><span>API LINK</span><b>ONLINE</b></div>
      <div class="system-state"><span>${user.real_name}</span><b>${user.role_name}</b></div>
      <nav class="nav">
        ${visibleNavItems.map(([href, label], index) => `<a data-code="${String(index + 1).padStart(2, "0")}" class="${href === page ? "active" : ""}" href="${href}">${label}</a>`).join("")}
      </nav>
      <button class="secondary logout-button" onclick="logout()">退出登录</button>
    </aside>
    <main class="main" id="mainContent"></main>
  `;
}

function mount(content) {
  const target = $("#mainContent");
  if (target) target.innerHTML = content;
}

function serializeForm(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const key of Object.keys(data)) {
    if (key.endsWith("_id") || ["role_id"].includes(key)) data[key] = Number(data[key]);
    if (["target_temperature", "safe_min", "safe_max", "upper_limit", "lower_limit", "temperature"].includes(key)) data[key] = Number(data[key]);
  }
  return data;
}

function fillForm(form, data) {
  Object.entries(data).forEach(([key, value]) => {
    const field = form.elements[key];
    if (field && value !== null && value !== undefined) field.value = value;
  });
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

let timeRangeCache = null;

async function getTimeRange() {
  if (timeRangeCache) return timeRangeCache;
  const now = new Date();
  const fallback = {
    min_year: now.getFullYear(),
    max_year: now.getFullYear(),
    min_month: pad2(now.getMonth() + 1),
    max_month: pad2(now.getMonth() + 1),
  };
  try {
    const data = await api("/api/analytics/time-range");
    timeRangeCache = {
      min_year: Number(data.min_year) || fallback.min_year,
      max_year: Number(data.max_year) || fallback.max_year,
      min_month: data.min_month || fallback.min_month,
      max_month: data.max_month || fallback.max_month,
    };
  } catch (error) {
    timeRangeCache = fallback;
  }
  return timeRangeCache;
}

async function initMonthFilters(prefix = "chart") {
  const range = await getTimeRange();
  const startYear = Number(range.min_year);
  const endYear = Number(range.max_year);
  const years = Array.from({ length: Math.max(1, endYear - startYear + 1) }, (_, index) => startYear + index);
  const yearOptions = years.map((year) => `<option value="${year}">${year}年</option>`).join("");
  const monthOptions = Array.from({ length: 12 }, (_, index) => {
    const value = pad2(index + 1);
    return `<option value="${value}">${value}月</option>`;
  }).join("");
  ["Start", "End"].forEach((name) => {
    const year = $(`#${prefix}${name}Year`);
    const month = $(`#${prefix}${name}Month`);
    if (!year || !month) return;
    if (year.tagName === "SELECT") {
      year.innerHTML = yearOptions;
    } else {
      year.placeholder = "年份";
    }
    month.innerHTML = monthOptions;
    year.value = name === "Start" ? String(range.min_year) : String(range.max_year);
    month.value = name === "Start" ? range.min_month : range.max_month;
  });
  const hint = $(`#${prefix}RangeHint`);
  if (hint) {
    hint.textContent = `数据范围：${range.min_year}年${range.min_month}月 - ${range.max_year}年${range.max_month}月`;
  }
}

function monthBoundary(prefix, name, mode) {
  const year = $(`#${prefix}${name}Year`)?.value;
  const month = $(`#${prefix}${name}Month`)?.value;
  if (!year || !month) return "";
  if (mode === "start") return `${year}-${month}-01`;
  const lastDay = new Date(Number(year), Number(month), 0).getDate();
  return `${year}-${month}-${pad2(lastDay)}`;
}

async function loadDevicesIntoSelect(selector = "select[name='device_id']") {
  const data = await api("/api/devices");
  document.querySelectorAll(selector).forEach((select) => {
    select.innerHTML = data.items.map((item) => `<option value="${item.id}">${item.device_code} ${item.device_name}</option>`).join("");
  });
}

function rowsTable(columns, rows, actions = () => "") {
  if (!rows.length) {
    return `<div class="table-wrap"><table><thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}<th>操作</th></tr></thead><tbody><tr><td colspan="${columns.length + 1}">暂无记录，等待设备数据或审批任务进入系统。</td></tr></tbody></table></div>`;
  }
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${columns.map((c) => `<th>${c.label}</th>`).join("")}<th>操作</th></tr></thead>
        <tbody>
          ${rows.map((row) => `<tr>${columns.map((c) => `<td>${c.render ? c.render(row) : (row[c.key] ?? "")}</td>`).join("")}<td>${actions(row)}</td></tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function initLogin() {
  const form = $("#loginForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const user = await api("/api/auth/login", { method: "post", data: serializeForm(form) });
      localStorage.setItem("temperature_user", JSON.stringify(user));
      location.href = "dashboard.html";
    } catch (error) {
      setMessage("登录失败，请检查账号密码。", "error");
    }
  });
}

async function initDashboard() {
  const adminLinks = isAdmin()
    ? `<a class="button" href="devices.html">设备建档</a>
       <a class="button secondary" href="approvals.html">审批中心</a>`
    : `<a class="button" href="change_requests.html">提交申请</a>`;
  mount(`
    <div class="topbar"><div><h1>温控参数审批工作台</h1><p>${isAdmin() ? "设备状态、审批积压、异常温度和参数变更痕迹集中监视。" : "查看设备数据、本人申请进度、异常温度和参数变更痕迹。"}</p></div><a class="button" href="change_requests.html">发起变更</a></div>
    <div class="grid four">
      <div class="stat"><span>温控设备</span><b id="deviceTotal">0</b></div>
      <div class="stat"><span>${isAdmin() ? "待审批" : "我的待处理申请"}</span><b id="pendingTotal">0</b></div>
      <div class="stat"><span>打开异常</span><b id="alarmTotal">0</b></div>
      <div class="stat"><span>采集记录</span><b id="readingTotal">0</b></div>
    </div>
    <div class="grid two dashboard-grid section-gap">
      <section class="panel quick-panel"><h2>审批作业链</h2><div class="toolbar">
        ${adminLinks}
        <a class="button secondary" href="visualization.html">统计图表</a>
        <a class="button secondary" href="monitor.html">温控看板</a>
      </div></section>
      <section class="panel recent-panel"><h2>最近参数落点</h2><div id="recentLogs"></div></section>
    </div>`);
  const data = await api("/api/dashboard");
  $("#deviceTotal").textContent = data.device_total;
  $("#pendingTotal").textContent = data.pending_total;
  $("#alarmTotal").textContent = data.alarm_total;
  $("#readingTotal").textContent = data.reading_total;
  $("#recentLogs").innerHTML = rowsTable(
    [
      { key: "device_name", label: "设备" },
      { key: "parameter_name", label: "参数" },
      { label: "变更", render: (r) => `${r.old_value} -> ${r.new_value}` },
      { label: "时间", render: (r) => String(r.changed_at || "").slice(5, 16) },
    ],
    data.recent_logs,
    () => ""
  );
}

async function initUsers() {
  mount(`
    <div class="topbar"><div><h1>用户管理</h1><p>用户增删改查、启用/禁用、密码重置、分页和搜索。</p></div></div>
    <section class="panel"><div class="toolbar"><input id="userSearch" placeholder="搜索账号或姓名"><button id="userSearchBtn">搜索</button><button class="secondary" id="userPrev">上一页</button><button class="secondary" id="userNext">下一页</button><span id="userPageInfo" class="message"></span></div></section>
    <section class="panel"><form id="userForm" class="form-grid">
      <input type="hidden" id="userEditId">
      <label>账号<input name="username" required></label>
      <label>姓名<input name="real_name" required></label>
      <label>密码<input name="password" value="123456" placeholder="编辑时留空表示不改密码"></label>
      <label>角色<select name="role_id"><option value="1">管理员</option><option value="2" selected>普通用户</option></select></label>
      <label>状态<select name="status"><option value="enabled">enabled</option><option value="disabled">disabled</option></select></label>
      <button id="userSubmit">新增用户</button><button class="secondary" type="button" id="userCancelEdit">取消编辑</button><span id="message" class="message"></span>
    </form></section><div id="tableBox" class="section-gap"></div>`);
  let page = 1;
  async function load() {
    const q = encodeURIComponent($("#userSearch").value || "");
    const data = await api(`/api/users?q=${q}&page=${page}&page_size=8`);
    window._userRows = data.items;
    $("#userPageInfo").textContent = `第 ${data.page} 页 / 共 ${data.total} 条`;
    $("#tableBox").innerHTML = rowsTable(
      [
        { key: "username", label: "账号" },
        { key: "real_name", label: "姓名" },
        { key: "role_name", label: "角色" },
        { label: "状态", render: (r) => badge(r.status) },
        { key: "created_at", label: "创建时间" },
      ],
      data.items,
      (r) => `<button onclick="editUser(${r.id})">编辑</button> <button onclick="resetPassword(${r.id})">重置密码</button> <button class="secondary" onclick="toggleUserStatus(${r.id})">启用/禁用</button> <button class="danger" onclick="deleteUser(${r.id})">软删除</button>`
    );
  }
  $("#userForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = serializeForm(event.target);
    if (!payload.password) delete payload.password;
    const editId = $("#userEditId").value;
    await api(editId ? `/api/users/${editId}` : "/api/users", { method: editId ? "put" : "post", data: payload });
    event.target.reset();
    $("#userEditId").value = "";
    $("#userSubmit").textContent = "新增用户";
    setMessage(editId ? "用户已更新" : "用户已新增");
    load();
  });
  $("#userCancelEdit").addEventListener("click", () => { $("#userForm").reset(); $("#userEditId").value = ""; $("#userSubmit").textContent = "新增用户"; });
  $("#userSearchBtn").addEventListener("click", () => { page = 1; load(); });
  $("#userPrev").addEventListener("click", () => { page = Math.max(1, page - 1); load(); });
  $("#userNext").addEventListener("click", () => { page += 1; load(); });
  window.resetPassword = async (id) => { await api(`/api/users/${id}/reset-password`, { method: "post", data: { password: "123456" } }); setMessage("密码已重置为 123456"); };
  window.toggleUserStatus = async (id) => { await api(`/api/users/${id}/toggle-status`, { method: "post" }); load(); };
  window.deleteUser = async (id) => { await api(`/api/users/${id}`, { method: "delete" }); load(); };
  window.editUser = (id) => {
    const row = window._userRows.find((item) => item.id === id);
    if (!row) return;
    fillForm($("#userForm"), { ...row, role_id: row.role_name === "管理员" ? 1 : 2, password: "" });
    $("#userEditId").value = id;
    $("#userSubmit").textContent = "保存用户";
  };
  load();
}

async function initDevices() {
  mount(`
    <div class="topbar"><div><h1>温控设备管理</h1><p>设备建档、状态、温度边界和责任人维护，支持 Excel 导入导出。</p></div><div class="toolbar"><button onclick="exportDevices()">导出设备 Excel</button><button class="secondary" onclick="exportDeviceTemplate()">下载导入模板</button></div></div>
    <section class="panel"><div class="toolbar"><input id="deviceImportFile" type="file" accept=".xlsx"><button onclick="importDevices()">导入设备 Excel</button><span id="importMessage" class="message"></span></div></section>
    <section class="panel"><form id="deviceForm" class="form-grid">
      <input type="hidden" id="deviceEditId">
      <label>设备编号<input name="device_code" required></label>
      <label>设备名称<input name="device_name" required></label>
      <label>车间<input name="workshop" required></label>
      <label>位置<input name="location" required></label>
      <label>状态<select name="status"><option>running</option><option>maintenance</option><option>stopped</option></select></label>
      <label>目标温度<input name="target_temperature" type="number" step="0.1" required></label>
      <label>安全下限<input name="safe_min" type="number" step="0.1" required></label>
      <label>安全上限<input name="safe_max" type="number" step="0.1" required></label>
      <label>负责人<input name="owner" required></label>
      <button id="deviceSubmit">新增设备</button><button class="secondary" type="button" id="deviceCancelEdit">取消编辑</button><span id="message" class="message"></span>
    </form></section><div id="tableBox" class="section-gap"></div>`);
  async function load() {
    const data = await api("/api/devices");
    window._deviceRows = data.items;
    $("#tableBox").innerHTML = rowsTable(
      [
        { key: "device_code", label: "编号" },
        { key: "device_name", label: "名称" },
        { key: "workshop", label: "车间" },
        { label: "状态", render: (r) => badge(r.status) },
        { label: "目标/范围", render: (r) => `${r.target_temperature}°C / ${r.safe_min}-${r.safe_max}°C` },
        { key: "owner", label: "负责人" },
      ],
      data.items,
      (r) => `<button onclick="editDevice(${r.id})">编辑</button> <button class="danger" onclick="deleteDevice(${r.id})">软删除</button>`
    );
  }
  $("#deviceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const editId = $("#deviceEditId").value;
    await api(editId ? `/api/devices/${editId}` : "/api/devices", { method: editId ? "put" : "post", data: serializeForm(event.target) });
    event.target.reset();
    $("#deviceEditId").value = "";
    $("#deviceSubmit").textContent = "新增设备";
    setMessage(editId ? "设备已更新" : "设备已新增");
    load();
  });
  $("#deviceCancelEdit").addEventListener("click", () => { $("#deviceForm").reset(); $("#deviceEditId").value = ""; $("#deviceSubmit").textContent = "新增设备"; });
  window.exportDevices = () => downloadFile("/api/export/devices.xlsx");
  window.exportDeviceTemplate = () => downloadFile("/api/export/device-template.xlsx");
  window.importDevices = async () => {
    const file = $("#deviceImportFile").files[0];
    if (!file) { $("#importMessage").textContent = "请选择 xlsx 文件"; return; }
    const form = new FormData();
    form.append("file", file);
    const result = await axios.post(`${API_BASE}/api/import/devices.xlsx`, form, { headers: { "Content-Type": "multipart/form-data" } });
    $("#importMessage").textContent = result.data.message;
    load();
  };
  window.deleteDevice = async (id) => { await api(`/api/devices/${id}`, { method: "delete" }); load(); };
  window.editDevice = (id) => {
    const row = window._deviceRows.find((item) => item.id === id);
    if (!row) return;
    fillForm($("#deviceForm"), row);
    $("#deviceEditId").value = id;
    $("#deviceSubmit").textContent = "保存设备";
  };
  load();
}

async function initPoints() {
  mount(`
    <div class="topbar"><div><h1>参数/测点管理</h1><p>维护温度采集点和参数阈值，支持测点 Excel 导入导出。</p></div><div class="toolbar"><button onclick="exportPoints()">导出测点 Excel</button><button class="secondary" onclick="exportPointTemplate()">下载测点模板</button></div></div>
    <section class="panel"><div class="toolbar"><input id="pointImportFile" type="file" accept=".xlsx"><button onclick="importPoints()">导入测点 Excel</button><span id="pointImportMessage" class="message"></span></div></section>
    <section class="panel"><form id="pointForm" class="form-grid">
      <input type="hidden" id="pointEditId">
      <label>所属设备<select name="device_id"></select></label>
      <label>测点编号<input name="point_code" required></label>
      <label>测点名称<input name="point_name" required></label>
      <label>参数名<input name="parameter_name" value="temperature" required></label>
      <label>单位<input name="unit" value="C" required></label>
      <label>上限<input name="upper_limit" type="number" step="0.1" required></label>
      <label>下限<input name="lower_limit" type="number" step="0.1" required></label>
      <button id="pointSubmit">新增测点</button><button class="secondary" type="button" id="pointCancelEdit">取消编辑</button><span id="message" class="message"></span>
    </form></section><div id="tableBox" class="section-gap"></div>`);
  await loadDevicesIntoSelect();
  async function load() {
    const data = await api("/api/points");
    window._pointRows = data.items;
    $("#tableBox").innerHTML = rowsTable(
      [
        { key: "device_name", label: "设备" },
        { key: "point_code", label: "测点编号" },
        { key: "point_name", label: "测点名称" },
        { key: "parameter_name", label: "参数" },
        { label: "范围", render: (r) => `${r.lower_limit}-${r.upper_limit}${r.unit}` },
      ],
      data.items,
      (r) => `<button onclick="editPoint(${r.id})">编辑</button> <button class="danger" onclick="deletePoint(${r.id})">软删除</button>`
    );
  }
  $("#pointForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const editId = $("#pointEditId").value;
    await api(editId ? `/api/points/${editId}` : "/api/points", { method: editId ? "put" : "post", data: serializeForm(event.target) });
    event.target.reset();
    $("#pointEditId").value = "";
    $("#pointSubmit").textContent = "新增测点";
    await loadDevicesIntoSelect();
    setMessage(editId ? "测点已更新" : "测点已新增");
    load();
  });
  $("#pointCancelEdit").addEventListener("click", async () => { $("#pointForm").reset(); $("#pointEditId").value = ""; $("#pointSubmit").textContent = "新增测点"; await loadDevicesIntoSelect(); });
  window.deletePoint = async (id) => { await api(`/api/points/${id}`, { method: "delete" }); load(); };
  window.editPoint = (id) => {
    const row = window._pointRows.find((item) => item.id === id);
    if (!row) return;
    fillForm($("#pointForm"), row);
    $("#pointEditId").value = id;
    $("#pointSubmit").textContent = "保存测点";
  };
  window.exportPoints = () => downloadFile("/api/export/points.xlsx");
  window.exportPointTemplate = () => downloadFile("/api/export/point-template.xlsx");
  window.importPoints = async () => {
    const file = $("#pointImportFile").files[0];
    if (!file) { $("#pointImportMessage").textContent = "请选择 xlsx 文件"; return; }
    const form = new FormData();
    form.append("file", file);
    const result = await axios.post(`${API_BASE}/api/import/points.xlsx`, form, { headers: { "Content-Type": "multipart/form-data" } });
    $("#pointImportMessage").textContent = result.data.message;
    load();
  };
  load();
}

async function initChangeRequests() {
  mount(`
    <div class="topbar"><div><h1>参数变更申请</h1><p>普通用户发起参数调整申请，管理员审批后才写入变更记录。</p></div></div>
    <section class="panel"><form id="requestForm" class="form-grid">
      <label>设备<select name="device_id"></select></label>
      <label>参数<select name="parameter_name"><option>target_temperature</option><option>safe_min</option><option>safe_max</option></select></label>
      <label>原值<input name="old_value" required></label>
      <label>新值<input name="new_value" required></label>
      <label>申请人<input value="${currentUser().real_name}" disabled></label>
      <label>优先级<select name="priority"><option>normal</option><option>high</option></select></label>
      <label class="span-2">原因<textarea name="reason" required></textarea></label>
      <button>提交申请</button><span id="message" class="message"></span>
    </form></section><div id="tableBox" class="section-gap"></div>`);
  await loadDevicesIntoSelect();
  async function load() {
    const data = await api("/api/change-requests");
    $("#tableBox").innerHTML = rowsTable(
      [
        { key: "device_name", label: "设备" },
        { key: "parameter_name", label: "参数" },
        { label: "变更", render: (r) => `${r.old_value} -> ${r.new_value}` },
        { key: "applicant_name", label: "申请人" },
        { label: "状态", render: (r) => badge(r.status) },
        { key: "reason", label: "原因" },
      ],
      data.items
    );
  }
  $("#requestForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/change-requests", { method: "post", data: serializeForm(event.target) });
    setMessage("申请已提交");
    load();
  });
  load();
}

async function initApprovals() {
  mount(`
    <div class="topbar"><div><h1>审批中心</h1><p>处理待审批参数变更，审批通过后生成参数变更记录。</p></div></div>
    <div id="message" class="message"></div><div id="pendingBox"></div><div id="approvalBox" class="section-gap"></div>`);
  async function load() {
    const pending = await api("/api/change-requests?status_filter=pending");
    $("#pendingBox").innerHTML = rowsTable(
      [
        { key: "device_name", label: "设备" },
        { key: "parameter_name", label: "参数" },
        { label: "变更", render: (r) => `${r.old_value} -> ${r.new_value}` },
        { key: "reason", label: "原因" },
      ],
      pending.items,
      (r) => `<button onclick="approve(${r.id}, 'approved')">通过</button> <button class="danger" onclick="approve(${r.id}, 'rejected')">驳回</button>`
    );
    const approvals = await api("/api/approvals");
    $("#approvalBox").innerHTML = rowsTable(
      [
        { key: "device_name", label: "设备" },
        { key: "parameter_name", label: "参数" },
        { label: "动作", render: (r) => badge(r.action) },
        { key: "approver_name", label: "审批人" },
        { key: "comment", label: "意见" },
        { key: "approved_at", label: "时间" },
      ],
      approvals.items
    );
  }
  window.approve = async (id, action) => {
    await api(`/api/approvals/${id}`, { method: "post", data: { action, comment: action === "approved" ? "同意变更" : "资料不足" } });
    setMessage("审批已处理");
    load();
  };
  load();
}

async function initChangeLogs() {
  mount(`<div class="topbar"><div><h1>变更记录</h1><p>只记录审批通过后的参数变更，可用于追溯。</p></div></div><div id="tableBox"></div>`);
  const data = await api("/api/change-logs");
  $("#tableBox").innerHTML = rowsTable(
    [
      { key: "device_name", label: "设备" },
      { key: "parameter_name", label: "参数" },
      { label: "变更", render: (r) => `${r.old_value} -> ${r.new_value}` },
      { key: "changed_by_name", label: "执行人" },
      { key: "changed_at", label: "时间" },
    ],
    data.items
  );
}

async function initDataQuery() {
  mount(`
    <div class="topbar"><div><h1>数据列表查询</h1><p>温度记录分页查询，可按设备筛选。</p></div><button onclick="exportReadings()">导出温度记录 Excel</button></div>
    <section class="panel"><div class="toolbar query-filters">
      <label class="month-control"><span>设备</span><span class="month-selects single"><select id="deviceFilter"></select></span></label>
      <label class="month-control"><span>状态</span><span class="month-selects single"><select id="statusFilter"><option value="">全部状态</option><option value="normal">normal</option><option value="warning">warning</option></select></span></label>
      <label class="month-control"><span>开始年月</span><span class="month-selects"><input id="queryStartYear" class="year-input" type="number" step="1" inputmode="numeric"><select id="queryStartMonth"></select></span></label>
      <label class="month-control"><span>结束年月</span><span class="month-selects"><input id="queryEndYear" class="year-input" type="number" step="1" inputmode="numeric"><select id="queryEndMonth"></select></span></label>
      <button id="filterBtn">筛选</button><button class="secondary" id="prevPage">上一页</button><button class="secondary" id="nextPage">下一页</button><span id="readingPageInfo" class="message"></span>
      <span id="queryRangeHint" class="range-hint"></span>
    </div></section>
    <div id="tableBox" class="section-gap"></div>`);
  await loadDevicesIntoSelect("#deviceFilter");
  await initMonthFilters("query");
  let page = 1;
  async function load() {
    const deviceId = $("#deviceFilter").value;
    const status = encodeURIComponent($("#statusFilter").value || "");
    const start = encodeURIComponent(monthBoundary("query", "Start", "start"));
    const end = encodeURIComponent(monthBoundary("query", "End", "end"));
    const data = await api(`/api/readings?device_id=${deviceId}&status_filter=${status}&start=${start}&end=${end}&page=${page}&page_size=50`);
    $("#readingPageInfo").textContent = `第 ${data.page} 页 / 共 ${data.total} 条`;
    $("#tableBox").innerHTML = rowsTable(
      [
        { key: "device_name", label: "设备" },
        { key: "temperature", label: "温度" },
        { key: "target_temperature", label: "目标温度" },
        { label: "状态", render: (r) => badge(r.status) },
        { key: "recorded_at", label: "记录时间" },
      ],
      data.items
    );
  }
  $("#filterBtn").addEventListener("click", () => { page = 1; load(); });
  $("#prevPage").addEventListener("click", () => { page = Math.max(1, page - 1); load(); });
  $("#nextPage").addEventListener("click", () => { page += 1; load(); });
  window.exportReadings = () => downloadFile("/api/export/readings.xlsx");
  load();
}

async function initVisualization() {
  mount(`
    <div class="topbar"><div><h1>数据可视化统计</h1><p>以温度偏差、审批积压、参数变更和异常事件为主线的工业分析看板。</p></div><div class="toolbar viz-filters">
      <label class="month-control"><span>趋势设备</span><span class="month-selects single"><select id="chartDeviceFilter"></select></span></label>
      <label class="month-control"><span>开始年月</span><span class="month-selects"><input id="chartStartYear" class="year-input" type="number" step="1" inputmode="numeric"><select id="chartStartMonth"></select></span></label>
      <label class="month-control"><span>结束年月</span><span class="month-selects"><input id="chartEndYear" class="year-input" type="number" step="1" inputmode="numeric"><select id="chartEndMonth"></select></span></label>
      <button onclick="loadVisualization()">刷新图表</button>
      <button onclick="downloadChart('trendChart','温度趋势.png')">导出趋势 PNG</button>
      <button class="secondary" onclick="downloadChart('riskChart','设备风险.png')">导出风险 PNG</button>
      <button class="secondary" onclick="downloadChart('statusChart','审批状态.png')">导出审批 PNG</button>
      <span id="chartRangeHint" class="range-hint"></span>
    </div></div>
    <section class="viz-command">
      <div class="viz-kpi"><span>设备总数</span><b id="vizDevices">0</b><small>temperature devices</small></div>
      <div class="viz-kpi"><span>温度读数</span><b id="vizReadings">0</b><small>readings captured</small></div>
      <div class="viz-kpi hot"><span>最大偏差</span><b id="vizMaxDeviation">0</b><small>actual - target</small></div>
      <div class="viz-kpi warn"><span>异常读数</span><b id="vizAbnormal">0</b><small>non-normal status</small></div>
    </section>
    <div class="viz-layout">
      <section class="panel viz-wide"><h2>温度趋势与偏差带</h2><div id="trendChart" class="chart chart-large"></div></section>
      <section class="panel"><h2>审批状态环</h2><div id="statusChart" class="chart"></div></section>
      <section class="panel"><h2>设备偏差风险排行</h2><div id="riskChart" class="chart"></div></section>
      <section class="panel"><h2>参数变更频次</h2><div id="parameterChart" class="chart"></div></section>
      <section class="panel"><h2>审批优先级矩阵</h2><div id="priorityChart" class="chart"></div></section>
      <section class="panel"><h2>异常时间分布</h2><div id="alarmTimelineChart" class="chart"></div></section>
    </div>`);
  await loadDevicesIntoSelect("#chartDeviceFilter");
  await initMonthFilters();
  window.loadVisualization = renderCharts;
  renderCharts();
}

async function renderCharts() {
  const summary = await api("/api/analytics/summary");
  const start = encodeURIComponent(monthBoundary("chart", "Start", "start"));
  const end = encodeURIComponent(monthBoundary("chart", "End", "end"));
  const deviceId = encodeURIComponent($("#chartDeviceFilter")?.value || "");
  const trends = await api(`/api/analytics/trends?limit=160&device_id=${deviceId}&start=${start}&end=${end}`);
  const overview = summary.overview || {};
  $("#vizDevices").textContent = overview.device_total ?? 0;
  $("#vizReadings").textContent = overview.reading_total ?? 0;
  $("#vizMaxDeviation").textContent = `${overview.max_deviation ?? 0}°C`;
  $("#vizAbnormal").textContent = overview.abnormal_readings ?? 0;
  const palette = ["#62d6c6", "#d59a4c", "#e06455", "#82c98d", "#8eb8ff"];
  const textStyle = { color: "#cfe0da", fontFamily: "Cascadia Code, Cascadia Mono, Consolas, monospace" };
  const axisLine = { lineStyle: { color: "rgba(188,203,194,.28)" } };
  const splitLine = { lineStyle: { color: "rgba(188,203,194,.12)" } };
  echarts.init($("#statusChart")).setOption({
    color: palette,
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle },
    series: [{
      name: "审批状态",
      type: "pie",
      radius: ["48%", "72%"],
      center: ["50%", "45%"],
      label: { color: "#f2f0e8", formatter: "{b}\n{c}" },
      itemStyle: { borderColor: "#0e1414", borderWidth: 3 },
      data: summary.request_status.map((x) => ({ name: x.status, value: x.total })),
    }],
  });
  echarts.init($("#parameterChart")).setOption({
    color: ["#62d6c6"],
    tooltip: { trigger: "axis" },
    grid: { left: 42, right: 18, top: 24, bottom: 72 },
    xAxis: { type: "category", axisLabel: { color: "#9aa8a1", rotate: 24 }, axisLine, data: summary.parameters.map((x) => x.parameter_name) },
    yAxis: { type: "value", axisLabel: textStyle, splitLine },
    series: [{ name: "变更次数", type: "bar", barWidth: 24, data: summary.parameters.map((x) => x.total), itemStyle: { borderRadius: [6, 6, 0, 0] } }],
  });
  const trendValues = trends.items.flatMap((x) => [Number(x.temperature), Number(x.target_temperature)]).filter(Number.isFinite);
  const deviations = trends.items.map((x) => Number(x.deviation)).filter(Number.isFinite);
  const tempMin = trendValues.length ? Math.floor((Math.min(...trendValues) - 4) / 5) * 5 : 0;
  const tempMax = trendValues.length ? Math.ceil((Math.max(...trendValues) + 4) / 5) * 5 : 100;
  const deviationLimit = deviations.length ? Math.max(4, Math.ceil(Math.max(...deviations.map(Math.abs)) / 2) * 2) : 8;
  echarts.init($("#trendChart")).setOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle },
    grid: [
      { left: 56, right: 54, top: 52, height: 260 },
      { left: 56, right: 54, bottom: 46, height: 72 },
    ],
    xAxis: [
      { type: "category", gridIndex: 0, axisLabel: { show: false }, axisTick: { show: false }, axisLine, data: trends.items.map((x) => x.recorded_at.slice(5, 16)) },
      { type: "category", gridIndex: 1, axisLabel: { color: "#9aa8a1" }, axisLine, data: trends.items.map((x) => x.recorded_at.slice(5, 16)) },
    ],
    yAxis: [
      { type: "value", gridIndex: 0, name: "温度 °C", min: tempMin, max: tempMax, axisLabel: textStyle, splitLine },
      { type: "value", gridIndex: 1, name: "偏差 °C", min: -deviationLimit, max: deviationLimit, axisLabel: textStyle, splitLine: { lineStyle: { color: "rgba(188,203,194,.08)" } } },
    ],
    series: [
      { name: "实际温度", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: trends.items.map((x) => x.temperature), smooth: true, symbol: "none", z: 3, lineStyle: { width: 2.4, color: "#62d6c6" }, areaStyle: { color: "rgba(98,214,198,.06)" } },
      { name: "目标温度", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: trends.items.map((x) => x.target_temperature), smooth: true, symbol: "none", silent: true, z: 1, lineStyle: { width: 1.4, color: "rgba(213,154,76,.62)", type: "dashed" } },
      { name: "偏差", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: trends.items.map((x) => x.deviation), barWidth: 4, itemStyle: { color: (p) => Math.abs(p.value) >= 6 ? "rgba(224,100,85,.9)" : "rgba(213,154,76,.56)" } },
    ],
  });
  echarts.init($("#riskChart")).setOption({
    color: ["#e06455", "#d59a4c"],
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle },
    grid: { left: 92, right: 24, top: 48, bottom: 28 },
    xAxis: { type: "value", axisLabel: textStyle, splitLine },
    yAxis: { type: "category", axisLabel: { color: "#cfe0da" }, axisLine, data: summary.device_risk.map((x) => x.device_name).reverse() },
    series: [
      { name: "平均偏差", type: "bar", data: summary.device_risk.map((x) => x.avg_deviation).reverse(), itemStyle: { borderRadius: [0, 6, 6, 0] } },
      { name: "异常数", type: "bar", data: summary.device_risk.map((x) => x.abnormal_total).reverse(), itemStyle: { borderRadius: [0, 6, 6, 0] } },
    ],
  });
  const priorities = [...new Set(summary.approval_priority.map((x) => x.priority))];
  const states = [...new Set(summary.approval_priority.map((x) => x.status))];
  const prioritySeries = states.map((state, index) => ({
    name: state,
    type: "bar",
    stack: "approval",
    data: priorities.map((priority) => summary.approval_priority.find((x) => x.priority === priority && x.status === state)?.total || 0),
    itemStyle: { color: palette[index % palette.length] },
  }));
  echarts.init($("#priorityChart")).setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, textStyle },
    grid: { left: 38, right: 18, top: 48, bottom: 34 },
    xAxis: { type: "category", axisLabel: textStyle, axisLine, data: priorities },
    yAxis: { type: "value", axisLabel: textStyle, splitLine },
    series: prioritySeries,
  });
  echarts.init($("#alarmTimelineChart")).setOption({
    color: ["#d59a4c"],
    tooltip: { trigger: "axis" },
    grid: { left: 44, right: 18, top: 24, bottom: 58 },
    xAxis: { type: "category", axisLabel: { color: "#9aa8a1", rotate: 30 }, axisLine, data: summary.alarm_timeline.map((x) => `${x.day.slice(5)} ${x.severity}`) },
    yAxis: { type: "value", axisLabel: textStyle, splitLine },
    series: [{ name: "异常数", type: "bar", data: summary.alarm_timeline.map((x) => x.total), itemStyle: { borderRadius: [6, 6, 0, 0], color: (p) => String(summary.alarm_timeline[p.dataIndex]?.severity).includes("critical") ? "#e06455" : "#d59a4c" } }],
  });
}

async function initAnalysis() {
  mount(`
    <div class="topbar"><div><h1>数据分析与挖掘</h1><p>页面展示统计结论，Notebook 负责完整 Pandas/Matplotlib 分析。</p></div><button onclick="exportAnalysisReport()">导出分析报告 CSV</button></div>
    <div class="grid three">
      <section class="panel"><h2>异常检测</h2><p>以温度与目标温度偏差大于 6°C 作为异常候选，用于生成报警事件。</p></section>
      <section class="panel"><h2>趋势预测</h2><p>基于最近温度序列计算移动平均，判断参数调整后是否趋稳。</p></section>
      <section class="panel"><h2>审批效率</h2><p>统计 pending、approved、rejected 比例，定位审批积压。</p></section>
    </div><section class="panel section-gap"><h2>统计 / 异常检测 / 趋势预测</h2><div id="analysisBox"></div></section>`);
  const summary = await api("/api/analytics/summary");
  const insights = await api("/api/analytics/insights");
  $("#analysisBox").innerHTML = `<pre>${JSON.stringify({ summary, insights, trend_prediction: insights.trend_prediction }, null, 2)}</pre>`;
  window.exportAnalysisReport = () => downloadFile("/api/export/analysis-report.csv");
}

async function initMonitor() {
  mount(`
    <div class="topbar"><div><h1>温控状态看板</h1><p>只参考工业 HMI 的实时状态、趋势、报警和日志，不加入水泵控制功能。</p></div></div>
    <div class="monitor-layout">
      <section class="panel">
        <div class="gauge-strip">
          <div class="gauge"><span>设备数量</span><b id="mDevice">0</b></div>
          <div class="gauge"><span>待审批</span><b id="mPending">0</b></div>
          <div class="gauge"><span>打开异常</span><b id="mAlarm">0</b></div>
        </div>
        <div id="monitorTrend" class="chart section-gap"></div>
      </section>
      <aside class="grid">
        <section class="panel"><h2>异常报警</h2><div id="alarmList"></div></section>
        <section class="panel"><h2>最近变更</h2><div id="monitorLogs"></div></section>
      </aside>
    </div>`);
  const dashboard = await api("/api/dashboard");
  $("#mDevice").textContent = dashboard.device_total;
  $("#mPending").textContent = dashboard.pending_total;
  $("#mAlarm").textContent = dashboard.alarm_total;
  $("#monitorLogs").innerHTML = dashboard.recent_logs.map((x) => `<p>${x.device_name}: ${x.parameter_name} ${x.old_value} -> ${x.new_value}</p>`).join("");
  const alarms = await api("/api/alarms?status_filter=open");
  $("#alarmList").innerHTML = alarms.items.map((x) => `<p>${badge(x.severity)} ${x.device_name}<br>${x.message}</p>`).join("");
  const trends = await api("/api/analytics/trends?limit=120");
  echarts.init($("#monitorTrend")).setOption({
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: trends.items.map((x) => x.recorded_at.slice(5, 16)) },
    yAxis: { type: "value" },
    series: [
      { name: "实际温度", type: "line", data: trends.items.map((x) => x.temperature), smooth: true, color: "#62d6c6" },
      { name: "目标温度", type: "line", data: trends.items.map((x) => x.target_temperature), smooth: true, color: "#d59a4c" },
    ],
  });
}

async function initSettings() {
  mount(`
    <div class="topbar"><div><h1>系统设置</h1><p>提交检查、接口文档和演示账号。</p></div></div>
    <div class="grid two">
      <section class="panel"><h2>演示账号</h2><p>管理员：admin / admin123</p><p>普通用户：user1 / 123456</p></section>
      <section class="panel"><h2>系统链接</h2><p><a class="button" href="http://127.0.0.1:8008/docs">FastAPI 文档</a></p><p>先启动 start_api.bat，再打开接口文档。scripts/seed_data.py 用于生成 1000+ 条测试数据。</p></section>
    </div>`);
}

document.addEventListener("DOMContentLoaded", () => {
  const page = currentPage();
  if (page !== "login") {
    if (!currentUser()) {
      location.href = "login.html";
      return;
    }
    if (adminPages.has(page) && !isAdmin()) {
      location.href = "dashboard.html";
      return;
    }
    renderShell();
  }
  const initializers = {
    login: initLogin,
    dashboard: initDashboard,
    users: initUsers,
    devices: initDevices,
    points: initPoints,
    change_requests: initChangeRequests,
    approvals: initApprovals,
    change_logs: initChangeLogs,
    data_query: initDataQuery,
    visualization: initVisualization,
    analysis: initAnalysis,
    monitor: initMonitor,
    settings: initSettings,
  };
  const init = initializers[page];
  if (init) init().catch((error) => setMessage(error.response?.data?.detail || error.message, "error"));
});
