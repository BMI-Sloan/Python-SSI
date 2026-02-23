"""
Confirm Elements — verify every known SSI tasks-page element is detectable.

Usage:
    Extra Parameters: {"url": "https://your-selectsales-url.com/path/to/tasks"}
"""

from utils.browser import make_driver, wait_for_page
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


FILTERS = {
    "ManufacturerFilter_I":   "Manufacturer filter input",
    "TskRetailerIDFilter_I":  "Retailer filter input",
    "TskTaskTypeIDFilter_I":  "Task Type filter input",
    "TskKeywordsFilter_I":    "Keywords filter input",
}

BUTTONS = {
    "ApproveButton_I":        "Approve button",
    "RejectButton_I":         "Reject button",
    "ExcelExport_I":          "Export to Excel button",
    "ClearTasksResults_I":    "Clear Filters button",
}

GRID = {
    "TasksResults":            "Results grid (container)",
    "TasksResults_DXSelAllBtn0": "Select-All checkbox",
    "TasksResults_DXSelBtn0":  "Row 1 checkbox",
}


def _check(driver, log, element_id: str, label: str) -> bool:
    elements = driver.find_elements(By.ID, element_id)
    if not elements:
        log(f"  ✗ MISSING   [{element_id}]  — {label}")
        return False
    el = elements[0]
    if el.is_displayed():
        tag   = el.tag_name
        etype = el.get_attribute("type") or ""
        val   = (el.get_attribute("value") or "")[:40]
        info  = f"<{tag}{' type=' + etype if etype else ''}>"
        if val:
            info += f"  value={val!r}"
        log(f"  ✓ FOUND     [{element_id}]  — {label}  {info}")
        return True
    else:
        log(f"  ~ HIDDEN    [{element_id}]  — {label}")
        return False


def run(log, excel_path, cookies, params):
    url = params.get("url", "").strip()
    if not url:
        log("[ERROR] No URL supplied.")
        log('[ERROR] Add {"url": "https://..."} in Extra Parameters.')
        return

    log(f"[INFO] Opening → {url}")
    try:
        driver = make_driver(cookies=cookies, headless=True, initial_url=url)
    except Exception as exc:
        log(f"[ERROR] Browser failed to start: {exc}")
        return

    try:
        log("[INFO] Waiting for page to load…")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "ApproveButton_I"))
            )
        except Exception:
            wait_for_page(driver, 4)

        log(f"[INFO] Page title : {driver.title}")
        log(f"[INFO] Current URL: {driver.current_url}")
        log("─" * 60)

        found = missing = 0
        log("[INFO] Filter inputs:")
        for eid, label in FILTERS.items():
            ok = _check(driver, log, eid, label)
            if ok: found += 1
            else: missing += 1

        log("─" * 60)
        log("[INFO] Action buttons:")
        for eid, label in BUTTONS.items():
            ok = _check(driver, log, eid, label)
            if ok: found += 1
            else: missing += 1

        log("─" * 60)
        log("[INFO] Grid elements:")
        for eid, label in GRID.items():
            ok = _check(driver, log, eid, label)
            if ok: found += 1
            else: missing += 1

        row_btns = driver.find_elements(By.CSS_SELECTOR, "[id^='TasksResults_DXSelBtn']")
        visible_rows = sum(1 for el in row_btns if el.is_displayed())
        log(f"  ✓ Row checkboxes visible: {visible_rows}")

        log("─" * 60)
        total = found + missing
        log(f"[RESULT] {found}/{total} elements found and visible.")
        if missing == 0:
            log("[RESULT] All elements confirmed — ready to automate.")
        else:
            log(f"[RESULT] {missing} element(s) not found — check the URL or page state.")

    finally:
        driver.quit()
        log("[INFO] Browser closed.")
