"""重新截取系统界面图（替换含学号的旧截图）。"""

from pathlib import Path

from playwright.sync_api import sync_playwright


EXPORTS = Path(__file__).resolve().parents[1] / "docs" / "exports"
BASE = "http://127.0.0.1:8008"

PAGES = {
    "zhang_ui_login_smoke.png": "/frontend/login.html",
    "zhang_ui_dashboard_smoke.png": "/frontend/dashboard.html",
    "zhang_ui_data_query_smoke.png": "/frontend/data_query.html",
    "zhang_ui_visualization_smoke.png": "/frontend/visualization.html",
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})

    page.goto(f"{BASE}/frontend/login.html", wait_until="networkidle")
    page.fill("#loginForm input[name=username]", "admin")
    page.fill("#loginForm input[name=password]", "admin123")
    page.click("#loginForm button")
    page.wait_for_url(f"{BASE}/frontend/dashboard.html", timeout=15000)
    page.wait_for_timeout(1500)

    for filename, path in PAGES.items():
        page.goto(f"{BASE}{path}", wait_until="networkidle")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(EXPORTS / filename))
        print("saved", filename)

    browser.close()
print("all screenshots updated")
