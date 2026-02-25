"""
Cancel PO — cancels one or more Purchase Orders in Select Sales.

The UI sidebar's PO Numbers box feeds po_numbers automatically.
Numbers shorter than 10 digits are padded with leading zeros by the UI.

Extra Parameters (JSON box in sidebar):
    {
        "url": "https://your-selectsales-url/path/to/po-search",

        // Optional: override auto-detected element IDs
        // Run inspect_page on the relevant URL to find the real IDs if
        // the script cannot locate an element automatically.
        "search_input_id":    "...",   // 'search for' input on PO list page
        "cancel_checkbox_id": "...",   // 'CANCEL PO' checkbox on detail page
        "save_button_id":     "..."    // 'Save' button on detail page
    }
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from utils.browser import make_driver, wait_for_page


# ── element discovery helpers ────────────────────────────────────────────────

def _first_visible(elements):
    return next((e for e in elements if e.is_displayed()), None)


def _by_id(driver, eid):
    if not eid:
        return None
    els = driver.find_elements(By.ID, eid)
    return _first_visible(els)


def find_search_input(driver, override_id=None):
    """Locate the 'search for' text input on the PO list/search page."""
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Input immediately after a label/cell whose text contains 'search for'
    el = _first_visible(driver.find_elements(By.XPATH,
        "//*[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
        "'search for')]/following::input[@type='text'][1]"))
    if el:
        return el

    # 3. Any visible text input with a placeholder hinting 'search'
    for inp in driver.find_elements(By.XPATH, "//input[@type='text']"):
        ph = (inp.get_attribute('placeholder') or '').lower()
        if 'search' in ph and inp.is_displayed():
            return inp

    # 4. Common DevExpress search input ID patterns
    for sid in ('PCSearchEdit_I', 'SearchEdit_I', 'POSearch_I',
                'GridSearch_I', 'Search_I', 'SearchBox_I'):
        el = _by_id(driver, sid)
        if el:
            return el

    return None


def find_po_in_results(driver, po_number):
    """
    Find the clickable row/link for po_number in the grid after searching.
    Tries an exact text match first, then a 'contains' fallback.
    """
    for xpath in (
        f"//a[normalize-space(text())='{po_number}']",
        f"//td[normalize-space(text())='{po_number}']",
        f"//td//span[normalize-space(text())='{po_number}']",
        f"//a[contains(text(),'{po_number}')]",
        f"//td[contains(text(),'{po_number}')]",
    ):
        el = _first_visible(driver.find_elements(By.XPATH, xpath))
        if el:
            return el
    return None


def find_cancel_checkbox(driver, override_id=None):
    """
    Locate the CANCEL PO checkbox on the PO detail page.
    Handles both standard HTML checkboxes and DevExpress-rendered ones.
    """
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Standard checkbox whose ID contains 'cancel' (case-insensitive via Python)
    for cb in driver.find_elements(By.XPATH, "//input[@type='checkbox']"):
        cb_id = (cb.get_attribute('id') or '').lower()
        if 'cancel' in cb_id:
            return cb

    # 3. Find a table row whose text contains 'cancel po', then grab the
    #    nearest checkbox — handles label-in-same-row layouts
    for row in driver.find_elements(By.XPATH, "//tr"):
        row_text = row.text.lower()
        if 'cancel po' in row_text or 'cancelpo' in row_text:
            cb = _first_visible(row.find_elements(By.XPATH, ".//input[@type='checkbox']"))
            if cb:
                return cb
            # DevExpress checkboxes may be rendered as a div/span with role=checkbox
            cb = _first_visible(row.find_elements(By.XPATH,
                ".//*[@role='checkbox'] | .//span[contains(@class,'checkbox')]"))
            if cb:
                return cb

    # 4. Common DevExpress-style ID patterns
    for cid in ('CancelPO_I', 'CancelOrder_I', 'POCancel_I', 'Cancel_I',
                'chkCancelPO', 'chkCancel'):
        el = _by_id(driver, cid)
        if el:
            return el

    return None


def find_save_button(driver, override_id=None):
    """Locate the Save button on the PO detail page."""
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Input button / submit whose value contains 'save'
    for btn in driver.find_elements(By.XPATH,
            "//input[@type='button' or @type='submit']"):
        val = (btn.get_attribute('value') or '').lower()
        if 'save' in val and btn.is_displayed():
            return btn

    # 3. <button> element whose text contains 'save'
    for btn in driver.find_elements(By.XPATH, "//button"):
        if 'save' in btn.text.lower() and btn.is_displayed():
            return btn

    # 4. <a> link whose text is 'save' (DevExpress toolbar links)
    el = _first_visible(driver.find_elements(By.XPATH,
        "//a[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'save')]"))
    if el:
        return el

    # 5. Common DevExpress ID patterns
    for sid in ('SaveButton_I', 'btnSave', 'SaveBtn_I', 'Save_I',
                'POSave_I', 'SaveChanges_I'):
        el = _by_id(driver, sid)
        if el:
            return el

    return None


# ── per-PO cancellation logic ────────────────────────────────────────────────

def _cancel_single_po(driver, log, base_url, po,
                      search_id, cancel_id, save_id):
    """Navigate to the PO list, search for po, open it, cancel it, save."""

    # ── Navigate back to the PO search page ──────────────────────────────
    log(f"  [INFO] Loading PO search page…")
    driver.get(base_url)
    wait_for_page(driver, 3)

    # ── Find the search input ─────────────────────────────────────────────
    log(f"  [INFO] Locating 'search for' input…")
    search_box = find_search_input(driver, search_id or None)
    if not search_box:
        raise RuntimeError(
            "Could not find the 'search for' input box. "
            "Run inspect_page on this URL, then add "
            '{"search_input_id": "<id>"} to Extra Parameters.'
        )

    search_box.clear()
    search_box.send_keys(po)
    log(f"  [INFO] Typed {po} — pressing Enter…")
    search_box.send_keys(Keys.RETURN)
    time.sleep(2)          # let the grid filter/refresh

    # ── Find the PO row in the results grid ───────────────────────────────
    log(f"  [INFO] Scanning results for PO {po}…")
    po_el = find_po_in_results(driver, po)
    if not po_el:
        raise RuntimeError(
            f"PO {po} not found in the results grid after searching. "
            "Verify the PO number exists and is visible on this page."
        )

    log(f"  [INFO] Found PO {po} — clicking to open detail…")
    po_el.click()
    wait_for_page(driver, 3)
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Tick the CANCEL PO checkbox ───────────────────────────────────────
    log(f"  [INFO] Locating CANCEL PO checkbox…")
    cancel_cb = find_cancel_checkbox(driver, cancel_id or None)
    if not cancel_cb:
        raise RuntimeError(
            "Could not find the CANCEL PO checkbox. "
            "Run inspect_page on the PO detail page, then add "
            '{"cancel_checkbox_id": "<id>"} to Extra Parameters.'
        )

    if cancel_cb.is_selected():
        log(f"  [INFO] CANCEL PO checkbox already checked — skipping tick.")
    else:
        cancel_cb.click()
        log(f"  [INFO] CANCEL PO checkbox ticked.")
    time.sleep(0.5)

    # ── Click Save ────────────────────────────────────────────────────────
    log(f"  [INFO] Locating Save button…")
    save_btn = find_save_button(driver, save_id or None)
    if not save_btn:
        raise RuntimeError(
            "Could not find the Save button. "
            "Run inspect_page on the PO detail page, then add "
            '{"save_button_id": "<id>"} to Extra Parameters.'
        )

    save_btn.click()
    log(f"  [INFO] Save clicked — waiting for page response…")
    time.sleep(2)
    log(f"  [INFO] Post-save URL: {driver.current_url}")


# ── main entry point ─────────────────────────────────────────────────────────

def run(log, excel_path, cookies, params):
    url = params.get("url", "").strip()
    if not url:
        log("[ERROR] No URL supplied.")
        log('[ERROR] Add {"url": "https://..."} in Extra Parameters.')
        return

    # po_numbers is injected by the UI from the PO Numbers textarea,
    # already padded to 10 chars.  Accept a plain list or newline string.
    raw = params.get("po_numbers", [])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split("\n") if p.strip()]
    po_numbers = [str(p).strip().zfill(10) for p in raw if str(p).strip()]

    if not po_numbers:
        log("[ERROR] No PO numbers provided.")
        log("[ERROR] Enter PO numbers in the PO Numbers box in the sidebar.")
        return

    # Optional element-ID overrides
    search_id = params.get("search_input_id", "")
    cancel_id = params.get("cancel_checkbox_id", "")
    save_id   = params.get("save_button_id", "")

    log(f"[INFO] POs to cancel ({len(po_numbers)}):")
    for n in po_numbers:
        log(f"  • {n}")
    log("─" * 60)

    log("[INFO] Starting browser…")
    try:
        driver = make_driver(cookies=cookies, headless=True, initial_url=url)
    except Exception as exc:
        log(f"[ERROR] Browser failed to start: {exc}")
        return

    success_count = 0
    fail_count    = 0

    try:
        for i, po in enumerate(po_numbers, 1):
            log(f"[INFO] ({i}/{len(po_numbers)}) Processing PO: {po}")
            try:
                _cancel_single_po(
                    driver, log, url, po,
                    search_id, cancel_id, save_id
                )
                success_count += 1
                log(f"  [SUCCESS] PO {po} cancelled.")
            except Exception as exc:
                fail_count += 1
                log(f"  [ERROR] PO {po} — {exc}")
            log("─" * 60)

    finally:
        driver.quit()
        log("[INFO] Browser closed.")

    log(f"[RESULT] Finished — {success_count} cancelled, {fail_count} failed.")
