"""
Partial PO — for each PO, sets every product line's ORDERED qty to match
its SHIPPED qty, then saves.

Enter PO numbers in the PO Numbers box in the sidebar.
No URL or Extra Parameters are needed — the site address is built in.

Steps (mirrors the Chrome Recorder recording):
  1. Navigate to http://edw.select-sales.com/
  2. Click the POs navigation link
  3. Type the PO number into #POKeywordsFilter_I and press Enter
  4. Click the first result row (#POResults_DXDataRow0 td:nth-of-type(3))
  5. For every product row where ORDERED ≠ SHIPPED:
       a. Click the ORDERED cell (td:nth-of-type(15) = index 14)
       b. Wait for #POProducts_DXEditor13_I to appear
       c. Type the SHIPPED value, press Enter twice
  6. Click Save (#EditFormButton_CD)
  7. Repeat from step 3 for the next PO number
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)

from utils.browser import make_driver

# ── site constants — confirmed by Chrome Recorder ────────────────────────────
_HOME        = 'http://edw.select-sales.com/'
_PO_LIST     = 'http://edw.select-sales.com/PO'
_POS_NAV     = 'li:nth-of-type(6) > a'
_SEARCH      = 'POKeywordsFilter_I'
_ROW0_CELL   = '#POResults_DXDataRow0 > td:nth-of-type(3)'
_ORDERED_IDX = 14   # td:nth-of-type(15) — confirmed by recording
_SHIPPED_IDX = 15   # td immediately after ORDERED
_DX_EDITOR   = 'POProducts_DXEditor13_I'   # editor that appears when ORDERED is clicked
_SAVE        = 'EditFormButton_CD'
_WAIT        = 15


def _partial_single_po(driver, log, po, first):
    wait = WebDriverWait(driver, _WAIT)

    # ── Steps 1-2: navigate to home and click POs (first PO only) ─────────
    if first:
        log(f"  [INFO] Navigating to {_HOME}…")
        driver.get(_HOME)
        try:
            nav = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, _POS_NAV)))
            nav.click()
            wait.until(EC.url_contains('/PO'))
        except TimeoutException:
            raise RuntimeError(
                "Could not navigate to PO list. "
                "Check that the session cookie is still valid."
            )
    else:
        driver.get(_PO_LIST)

    # ── Step 3: type PO number and press Enter ────────────────────────────
    try:
        search = wait.until(EC.presence_of_element_located((By.ID, _SEARCH)))
    except TimeoutException:
        raise RuntimeError(
            f"#{_SEARCH} not found after {_WAIT}s "
            f"(current URL: {driver.current_url}). "
            "Session may have expired — re-paste the cookies and try again."
        )

    search.clear()
    search.send_keys(po)
    log(f"  [INFO] Typed {po} — pressing Enter…")
    search.send_keys(Keys.RETURN)

    # ── Step 4: click first result ────────────────────────────────────────
    try:
        row_cell = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, _ROW0_CELL)
        ))
    except TimeoutException:
        raise RuntimeError(f"PO {po} did not appear in results after {_WAIT}s.")

    log(f"  [INFO] Found result — opening PO detail…")
    row_cell.click()

    try:
        wait.until(EC.url_contains('/PO/Edit/'))
    except TimeoutException:
        raise RuntimeError(f"Detail page did not load after {_WAIT}s (URL: {driver.current_url}).")
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Wait for product table to render ─────────────────────────────────
    try:
        wait.until(EC.presence_of_element_located((By.ID, 'POProducts_DXDataRow0')))
    except TimeoutException:
        log(f"  [INFO] No product rows found — nothing to adjust.")
        return

    # ── Step 5: loop every product row, fix ORDERED where it differs ──────
    changes = 0
    row_n   = 0

    while True:
        row_id = f'POProducts_DXDataRow{row_n}'

        try:
            row_el = driver.find_element(By.ID, row_id)
        except NoSuchElementException:
            break   # no more rows

        cells = row_el.find_elements(By.TAG_NAME, 'td')
        if len(cells) <= _SHIPPED_IDX:
            row_n += 1
            continue

        ordered_text = cells[_ORDERED_IDX].text.strip()
        shipped_text = cells[_SHIPPED_IDX].text.strip()

        if not shipped_text:
            log(f"  [SKIP] Row {row_n+1}: SHIPPED is blank")
            row_n += 1
            continue

        if ordered_text == shipped_text:
            log(f"  [OK]   Row {row_n+1}: ORDERED={ordered_text} already matches SHIPPED")
            row_n += 1
            continue

        log(f"  [EDIT] Row {row_n+1}: ORDERED {ordered_text} → {shipped_text}")

        # Click the ORDERED cell — same as recording's td:nth-of-type(15)
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", cells[_ORDERED_IDX]
            )
            cells[_ORDERED_IDX].click()
            time.sleep(0.5)
        except StaleElementReferenceException:
            log(f"  [WARN] Row {row_n+1}: row became stale on click — skipping")
            row_n += 1
            continue

        # Wait for DevExpress editor to appear (#POProducts_DXEditor13_I)
        try:
            editor = wait.until(EC.presence_of_element_located((By.ID, _DX_EDITOR)))
        except TimeoutException:
            log(f"  [WARN] Row {row_n+1}: editor #{_DX_EDITOR} did not appear — skipping")
            row_n += 1
            continue

        # Type the SHIPPED value, then Enter × 2 (matches the recording exactly)
        editor.send_keys(Keys.CONTROL + 'a')
        editor.send_keys(shipped_text)
        editor.send_keys(Keys.ENTER)
        time.sleep(0.2)
        try:
            editor.send_keys(Keys.ENTER)
        except StaleElementReferenceException:
            pass   # editor dismissed after first Enter — that's fine
        time.sleep(0.3)

        changes += 1
        row_n += 1

    if changes == 0:
        log(f"  [INFO] All ORDERED quantities already match SHIPPED — nothing to save.")
        return

    # ── Step 6: click Save ────────────────────────────────────────────────
    log(f"  [INFO] {changes} row(s) updated — clicking Save…")
    try:
        save = wait.until(EC.element_to_be_clickable((By.ID, _SAVE)))
        save.click()
    except TimeoutException:
        raise RuntimeError(f"#{_SAVE} not found after {_WAIT}s.")

    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        pass
    log(f"  [INFO] Done. URL: {driver.current_url}")


# ── main entry point ──────────────────────────────────────────────────────────

def run(log, excel_path, cookies, params):
    raw = params.get("po_numbers", [])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split("\n") if p.strip()]
    po_numbers = [str(p).strip().zfill(10) for p in raw if str(p).strip()]

    if not po_numbers:
        log("[ERROR] No PO numbers provided. Enter them in the PO Numbers box.")
        return

    log(f"[INFO] POs to process ({len(po_numbers)}):")
    for n in po_numbers:
        log(f"  • {n}")
    log("─" * 60)

    log("[INFO] Starting browser…")
    try:
        driver = make_driver(cookies=cookies, headless=True, initial_url=_HOME)
    except Exception as exc:
        log(f"[ERROR] Browser failed to start: {exc}")
        return

    success_count = 0
    fail_count    = 0
    try:
        for i, po in enumerate(po_numbers, 1):
            log(f"[INFO] ({i}/{len(po_numbers)}) Processing PO: {po}")
            try:
                _partial_single_po(driver, log, po, first=(i == 1))
                success_count += 1
                log(f"  [SUCCESS] PO {po} done.")
            except Exception as exc:
                fail_count += 1
                log(f"  [ERROR] PO {po} — {exc}")
            log("─" * 60)
    finally:
        driver.quit()
        log("[INFO] Browser closed.")

    log(f"[RESULT] Finished — {success_count} done, {fail_count} failed.")
