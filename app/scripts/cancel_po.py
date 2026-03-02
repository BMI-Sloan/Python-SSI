"""
Cancel PO — cancels one or more Purchase Orders in Select Sales.

Enter PO numbers in the PO Numbers box in the sidebar.
No URL or Extra Parameters are needed — the site address is built in.

Steps (mirrors the Chrome Recorder recording exactly):
  1. Navigate to http://edw.select-sales.com/
  2. Click the POs navigation link
  3. Type the PO number into #POKeywordsFilter_I and press Enter
  4. Click the first result row (#POResults_DXDataRow0 td:nth-of-type(3))
  5. Click the Cancel PO checkbox (#CancelPo_S_D)
  6. Click Save (#EditFormButton_CD)
  7. Repeat from step 3 for the next PO number
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

from utils.browser import make_driver

# ── site constants — confirmed by Chrome Recorder ────────────────────────────
_HOME       = 'http://edw.select-sales.com/'
_PO_LIST    = 'http://edw.select-sales.com/PO'
_POS_NAV    = 'li:nth-of-type(6) > a'           # recording step 3
_SEARCH     = 'POKeywordsFilter_I'               # recording step 4
_ROW0_CELL  = '#POResults_DXDataRow0 > td:nth-of-type(3)'  # recording step 7
_CANCEL_CB  = 'CancelPo_S_D'                     # recording step 8
_SAVE       = 'EditFormButton_CD'                # recording step 9
_WAIT       = 15


def _go_to_po_list(driver):
    """Navigate directly to the PO list and wait for the search input."""
    driver.get(_PO_LIST)
    try:
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, _SEARCH))
        )
    except TimeoutException:
        pass   # best effort


def _cancel_single_po(driver, log, po, first):
    wait = WebDriverWait(driver, _WAIT)

    # ── Step 1-2: navigate to home and click POs nav (first PO only) ──────
    if first:
        log(f"  [INFO] Navigating to {_HOME}…")
        driver.get(_HOME)
        try:
            nav = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, _POS_NAV)))
            nav.click()
            wait.until(EC.url_contains('/PO'))
        except TimeoutException:
            raise RuntimeError(
                f"Could not navigate to PO list via home page. "
                "Check that the session cookie is still valid."
            )
    else:
        # Already on /PO from previous save — just reload it cleanly
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

    # ── Step 4: wait for first result and click it ────────────────────────
    try:
        row_cell = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, _ROW0_CELL)
        ))
    except TimeoutException:
        raise RuntimeError(f"PO {po} did not appear in results after {_WAIT}s.")

    log(f"  [INFO] Found result — opening PO detail…")
    # DevExpress re-renders the results grid after the search, which can
    # make the element stale between find and click.  Retry up to 3 times.
    for attempt in range(3):
        try:
            row_cell = driver.find_element(By.CSS_SELECTOR, _ROW0_CELL)
            ActionChains(driver).click(row_cell).perform()
            break
        except StaleElementReferenceException:
            if attempt == 2:
                raise RuntimeError(
                    f"Result row for PO {po} went stale 3 times — "
                    "DevExpress grid re-rendered unexpectedly."
                )
            time.sleep(0.3)

    # ── Wait for /PO/Edit/ page ───────────────────────────────────────────
    try:
        wait.until(EC.url_contains('/PO/Edit/'))
    except TimeoutException:
        raise RuntimeError(f"Detail page did not load after {_WAIT}s (URL: {driver.current_url}).")
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # Wait for the Save button — confirms the form is fully loaded before
    # we attempt to interact with any DevExpress controls on the page.
    try:
        wait.until(EC.presence_of_element_located((By.ID, _SAVE)))
    except TimeoutException:
        raise RuntimeError(f"Form did not fully load (#{_SAVE} missing after {_WAIT}s).")

    # ── Step 5: tick the CANCEL PO checkbox ──────────────────────────────
    # Recording confirms selector: #CancelPo_S_D (the DevExpress checkbox
    # visual element).  JavaScript el.click() dispatches an untrusted event
    # that DevExpress ignores for state changes.  Use Selenium ActionChains
    # for a trusted OS-level click, then verify the checkbox is checked.
    log(f"  [INFO] Setting CANCEL PO checkbox…")

    cb = None
    for sel in [
        (By.ID,          'CancelPo_S_D'),
        (By.CSS_SELECTOR,'#CancelPo_S_D'),
        (By.XPATH,       '//*[@id="CancelPo_S_D"]'),
    ]:
        try:
            cb = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(sel))
            break
        except TimeoutException:
            continue

    if cb is None:
        raise RuntimeError(
            "Could not find #CancelPo_S_D on the page. "
            "Open the PO manually and use browser Inspect to confirm "
            "the element still has id='CancelPo_S_D'."
        )

    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", cb)
    time.sleep(0.2)
    ActionChains(driver).click(cb).perform()
    time.sleep(0.5)

    # Verify the checkbox is now checked
    is_checked = driver.execute_script("""
        try {
            var ctrl = ASPxClientControl.GetControlCollection().GetByName('CancelPo');
            if (ctrl && typeof ctrl.GetChecked === 'function') return ctrl.GetChecked();
        } catch(e) {}
        var el = document.getElementById('CancelPo_S_D');
        if (!el) return null;
        var cls = el.className || '';
        return cls.indexOf('Checked') >= 0 || cls.indexOf('checked') >= 0;
    """)

    if is_checked is False:
        # One retry — sometimes DevExpress needs a second click to register
        ActionChains(driver).click(cb).perform()
        time.sleep(0.5)
        is_checked = driver.execute_script("""
            try {
                var ctrl = ASPxClientControl.GetControlCollection().GetByName('CancelPo');
                if (ctrl && typeof ctrl.GetChecked === 'function') return ctrl.GetChecked();
            } catch(e) {}
            var el = document.getElementById('CancelPo_S_D');
            if (!el) return null;
            var cls = el.className || '';
            return cls.indexOf('Checked') >= 0 || cls.indexOf('checked') >= 0;
        """)

    if is_checked is False:
        raise RuntimeError(
            "Clicked #CancelPo_S_D twice but checkbox is still unchecked. "
            "The DevExpress control may require a different interaction."
        )

    log(f"  [INFO] CANCEL PO checkbox confirmed checked.")
    time.sleep(0.2)

    # ── Step 6: click Save ────────────────────────────────────────────────
    try:
        save = wait.until(EC.element_to_be_clickable((By.ID, _SAVE)))
        save.click()
        log(f"  [INFO] Save clicked.")
    except TimeoutException:
        raise RuntimeError(f"#{_SAVE} not found after {_WAIT}s.")

    # ── Wait to return to PO list before processing next PO ──────────────
    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        pass  # save fired — continue anyway


# ── main entry point ──────────────────────────────────────────────────────────

def run(log, excel_path, cookies, params):
    raw = params.get("po_numbers", [])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split("\n") if p.strip()]
    po_numbers = [str(p).strip().zfill(10) for p in raw if str(p).strip()]

    if not po_numbers:
        log("[ERROR] No PO numbers provided. Enter them in the PO Numbers box.")
        return

    log(f"[INFO] POs to cancel ({len(po_numbers)}):")
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
                _cancel_single_po(driver, log, po, first=(i == 1))
                success_count += 1
                log(f"  [SUCCESS] PO {po} cancelled.")
            except Exception as exc:
                fail_count += 1
                log(f"  [ERROR] PO {po} — {exc}")
            finally:
                # Always reset to PO list after each PO — success or failure.
                # This guarantees the next PO starts from a clean state.
                try:
                    if '/PO/Edit/' in driver.current_url:
                        log(f"  [INFO] Resetting browser to PO list…")
                        _go_to_po_list(driver)
                except Exception:
                    pass
            log("─" * 60)

        # Final reset so the browser is on the PO list when the script finishes
        log("[INFO] All done — resetting to PO list.")
        _go_to_po_list(driver)

    finally:
        driver.quit()
        log("[INFO] Browser closed.")

    log(f"[RESULT] Finished — {success_count} cancelled, {fail_count} failed.")
