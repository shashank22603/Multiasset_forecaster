"""Exercise the running dashboard; saves reviewable desktop/mobile screenshots."""
import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

output = Path("artifacts")
output.mkdir(exist_ok=True)
project = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(project / ".cache/ms-playwright"))
local_libraries = project / ".cache/browser-libs/root/usr/lib/x86_64-linux-gnu"
if local_libraries.exists():
    os.environ["LD_LIBRARY_PATH"] = str(local_libraries) + (":" + os.environ["LD_LIBRARY_PATH"] if os.environ.get("LD_LIBRARY_PATH") else "")
with sync_playwright() as browser_engine:
    browser = browser_engine.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1480, "height": 1060}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8050", wait_until="networkidle")
    page.wait_for_selector(".asset-row", timeout=30000)
    assert page.locator("#interval").input_value() == "5m"
    assert page.locator(".asset-row").count() == 27
    page.wait_for_selector("#chart svg")
    assert page.locator("#chart [data-candle]").count() > 50
    page.locator('[data-filter="commodity"]').click()
    assert page.locator(".asset-row").count() == 5
    page.locator('#asset-list [data-asset="COM:GOLD"]').click()
    expect(page.locator("#chart-name")).to_have_text("Gold")
    page.locator("#search").fill("natural")
    assert page.locator(".asset-row").count() == 1
    page.locator("#search").fill("")
    page.locator('[data-filter="all"]').click()
    page.locator('#asset-list [data-asset="NSE:RELIANCE"]').click()
    expect(page.locator("#chart-name")).to_have_text("Reliance Industries")
    page.screenshot(path=str(output / "dashboard-5m-desktop.png"), full_page=True)
    page.locator("#interval").select_option("1d")
    expect(page.locator("#notice")).to_have_text(re.compile(r"^1d bars"), timeout=30000)
    page.wait_for_selector("#chart svg")
    page.locator("#interval").select_option("5m")
    expect(page.locator("#notice")).to_have_text(re.compile(r"^5m bars"), timeout=30000)
    page.wait_for_timeout(1500)
    page.locator('[data-view="experiments"]').click()
    page.locator('.run-row[data-run^="comparison-"]').first.click()
    page.wait_for_selector("#run-result table")
    assert page.locator("#run-result tbody tr").count() == 5
    page.locator('.run-row[data-run^="backtest-"]').first.click()
    page.wait_for_selector("#result-chart svg")
    page.screenshot(path=str(output / "evaluation-5m-desktop.png"), full_page=True)
    page.locator('[data-view="coverage"]').click()
    assert page.locator("#coverage-table tbody tr").count() == 27
    assert "RECENT 128 GAPS" in page.locator("#coverage-table").inner_text()
    if "--submit" in sys.argv:
        page.locator('[data-view="experiments"]').click()
        page.locator("#retrospective").check()
        page.locator("#latest-complete").check()
        page.locator("#run-context").fill("64")
        page.locator("#run-button").click()
        expect(page.locator("#job-status")).to_contain_text("Results saved", timeout=120000)
        expect(page.locator("#run-result")).to_contain_text("Lagged inputs")
    page.locator('[data-view="markets"]').click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(output / "dashboard-5m-mobile.png"), full_page=True)
    assert not errors, errors
    (output / "browser-validation.json").write_text(json.dumps({
        "passed": True, "javascript_errors": errors, "assets": 27, "real_model_job_submitted": "--submit" in sys.argv,
        "checks": ["five-minute default", "candles", "asset filters", "search", "asset selection", "daily/intraday switching", "comparison results", "actual-versus-predicted plot", "coverage table", "mobile overflow"],
    }, indent=2))
    browser.close()
print("Browser smoke passed; screenshots and validation saved under artifacts/")
