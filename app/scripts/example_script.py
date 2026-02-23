"""
Example script — template to copy when building real scripts.

Every script module must expose a single function:
    def run(log, excel_path, cookies, params):
        ...
"""

from utils.browser import make_driver, wait_for_page
from utils.excel_handler import read_excel


def run(log, excel_path, cookies, params):
    log("[INFO] Example script started.")

    rows = []
    if excel_path:
        log(f"[INFO] Reading Excel file: {excel_path}")
        rows = read_excel(excel_path)
        log(f"[INFO] Found {len(rows)} data rows.")
        for i, row in enumerate(rows[:3]):
            log(f"[INFO]   Row {i+1}: {row}")
    else:
        log("[WARN] No Excel file provided.")

    target_url = params.get("url", "https://example.com")
    log(f"[INFO] Opening browser → {target_url}")

    driver = make_driver(cookies=cookies, headless=True, initial_url=target_url)

    try:
        log(f"[INFO] Page title: {driver.title}")
        for i, row in enumerate(rows):
            log(f"[INFO] Processing row {i+1}: {row}")
        log("[INFO] All rows processed.")
    finally:
        driver.quit()
        log("[INFO] Browser closed.")
