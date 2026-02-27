"""
Cancel PO — cancels one or more Purchase Orders in Select Sales.

The UI sidebar's PO Numbers box feeds po_numbers automatically.
Numbers shorter than 10 digits are padded with leading zeros by the UI.

Extra Parameters (JSON box in sidebar):
    {
        "url": "http://edw.select-sales.com/PO"
    }

Element IDs are fixed (confirmed by Chrome Recorder) — no overrides needed.
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from utils.browser import make_driver

# ── element IDs confirmed by Chrome Recorder ─────────────────────────────────
# These IDs never change on edw.select-sales.com/PO — no fallback chains needed.
_SEARCH_INPUT   = 'POKeywordsFilter_I'
_RESULT_CELL    = '#POResults_DXDataRow0 > td:nth-of-type(3)'
_CANCEL_CB      = 'CancelPo_S_D'
_SAVE_BTN       = 'EditFormButton_CD'
_WAIT_SEC       = 15   # max seconds to wait for any element


# ── element discovery helpers ────────────────────────────────────────────────

def _first_visible(elements):
    return next((e for e in elements if e.is_displayed()), None)


def _by_id(driver, eid):
    if not eid:
        return None
    els = driver.find_elements(By.ID, eid)
    return _first_visible(els)


def _by_id_any(driver, eid):
    """Find element by ID without requiring visibility — needed for DevExpress
    widgets that render their state input as CSS-hidden but still interactable."""
    if not eid:
        return None
    els = driver.find_elements(By.ID, eid)
    return els[0] if els else None


def _find_id_in_elements(elements, *hints):
    """Return the first element ID from a stored page-library list whose ID
    contains any of the hint words (case-insensitive)."""
    for hint in hints:
        for el in elements:
            if hint.lower() in el.get('id', '').lower():
                return el.get('id')
    return None


def _elements_for_url(page_library, url):
    """Return the stored element list for the saved page whose base URL is the
    longest prefix match of `url`."""
    best, best_len = [], 0
    for page in page_library:
        base = page.get('url', '').rstrip('/')
        if base and url.startswith(base) and len(base) > best_len:
            best = page.get('elements', [])
            best_len = len(base)
    return best


def find_search_input(driver, override_id=None):
    """Locate the keywords filter input on the PO list page."""
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Known ID from site inspection
    el = _by_id(driver, 'POKeywordsFilter_I')
    if el:
        return el

    # 3. Input immediately after a label/cell whose text contains 'search for'
    el = _first_visible(driver.find_elements(By.XPATH,
        "//*[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
        "'search for')]/following::input[@type='text'][1]"))
    if el:
        return el

    # 4. Any visible text input with a placeholder hinting 'search'
    for inp in driver.find_elements(By.XPATH, "//input[@type='text']"):
        ph = (inp.get_attribute('placeholder') or '').lower()
        if 'search' in ph and inp.is_displayed():
            return inp

    # 5. Generic DevExpress search input ID patterns (fallback)
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
    The site uses DevExpress: CancelPo_I is the clickable element,
    CancelPo_S is the hidden state holder.
    """
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Known DevExpress checkbox IDs (confirmed by Chrome recorder).
    #    CancelPo_S_D is the clickable switch element; CancelPo_I / CancelPo_S
    #    are fallbacks.  Use _by_id_any because DevExpress renders state inputs
    #    as CSS-hidden, so is_displayed() returns False.
    for cid in ('CancelPo_S_D', 'CancelPo_I', 'CancelPo_S'):
        el = _by_id_any(driver, cid)
        if el:
            return el

    # 3. Any checkbox-type input whose ID contains 'cancel' (case-insensitive)
    for cb in driver.find_elements(By.XPATH, "//input[@type='checkbox']"):
        cb_id = (cb.get_attribute('id') or '').lower()
        if 'cancel' in cb_id:
            return cb

    # 4. Find a table row whose text contains 'cancel po', then grab the
    #    nearest checkbox — handles label-in-same-row layouts
    for row in driver.find_elements(By.XPATH, "//tr"):
        row_text = row.text.lower()
        if 'cancel po' in row_text or 'cancelpo' in row_text:
            cb = _first_visible(row.find_elements(By.XPATH, ".//input[@type='checkbox']"))
            if cb:
                return cb
            cb = _first_visible(row.find_elements(By.XPATH,
                ".//*[@role='checkbox'] | .//span[contains(@class,'checkbox')]"))
            if cb:
                return cb

    return None


def find_save_button(driver, override_id=None):
    """Locate the Save button on the PO detail page."""
    # 1. User-supplied ID override
    el = _by_id(driver, override_id)
    if el:
        return el

    # 2. Known IDs — EditFormButton_CD confirmed by Chrome recorder; _I kept as
    #    fallback in case the site variant differs.
    for bid in ('EditFormButton_CD', 'EditFormButton_I'):
        el = _by_id(driver, bid)
        if el:
            return el

    # 3. Input button / submit whose value contains 'save'
    for btn in driver.find_elements(By.XPATH,
            "//input[@type='button' or @type='submit']"):
        val = (btn.get_attribute('value') or '').lower()
        if 'save' in val and btn.is_displayed():
            return btn

    # 4. <button> element whose text contains 'save'
    for btn in driver.find_elements(By.XPATH, "//button"):
        if 'save' in btn.text.lower() and btn.is_displayed():
            return btn

    # 5. <a> link whose text is 'save' (DevExpress toolbar links)
    el = _first_visible(driver.find_elements(By.XPATH,
        "//a[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'save')]"))
    if el:
        return el

    return None


# ── per-PO cancellation logic ────────────────────────────────────────────────

def _cancel_single_po(driver, log, base_url, po):
    """Navigate to the PO list, search for po, open it, cancel it, save."""
    wait = WebDriverWait(driver, _WAIT_SEC)

    # ── Navigate to PO list ───────────────────────────────────────────────
    log(f"  [INFO] Loading {base_url}…")
    driver.get(base_url)

    # ── Wait for and fill the search input ───────────────────────────────
    try:
        search_box = wait.until(
            EC.presence_of_element_located((By.ID, _SEARCH_INPUT))
        )
    except TimeoutException:
        raise RuntimeError(
            f"Search input #{_SEARCH_INPUT} not found after {_WAIT_SEC}s "
            f"(current URL: {driver.current_url}). "
            "The page may not have loaded or the session cookie expired."
        )

    search_box.clear()
    search_box.send_keys(po)
    log(f"  [INFO] Typed {po} — pressing Enter…")
    search_box.send_keys(Keys.RETURN)

    # ── Wait for and click the first result row ───────────────────────────
    try:
        result_cell = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _RESULT_CELL))
        )
    except TimeoutException:
        raise RuntimeError(
            f"PO {po} not found in results grid after {_WAIT_SEC}s. "
            "Verify the PO number exists on this site."
        )

    log(f"  [INFO] Found PO {po} — opening detail page…")
    result_cell.click()

    # ── Wait for the detail page to load ─────────────────────────────────
    try:
        wait.until(EC.url_contains('/PO/Edit/'))
    except TimeoutException:
        raise RuntimeError(
            f"PO detail page did not load after {_WAIT_SEC}s "
            f"(current URL: {driver.current_url})."
        )
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Tick the CANCEL PO checkbox ───────────────────────────────────────
    # Try the DevExpress JS API first (cleanest approach), then fall back
    # to clicking the confirmed element #CancelPo_S_D directly.
    log(f"  [INFO] Setting CANCEL PO checkbox…")
    js_result = driver.execute_script("""
        try {
            var ctrl = ASPxClientControl.GetControlCollection().GetByName('CancelPo');
            if (!ctrl || typeof ctrl.GetChecked !== 'function')
                return {ok: false, reason: 'control not found'};
            var was = ctrl.GetChecked();
            if (!was) ctrl.SetChecked(true);
            return {ok: true, was_checked: was};
        } catch(e) {
            return {ok: false, reason: String(e)};
        }
    """)

    if isinstance(js_result, dict) and js_result.get('ok'):
        if js_result.get('was_checked'):
            log("  [INFO] Already checked — skipping.")
        else:
            log("  [INFO] Checkbox set via DevExpress API.")
    else:
        reason = (js_result or {}).get('reason', str(js_result))
        log(f"  [DEBUG] DevExpress API: {reason} — clicking #{_CANCEL_CB} directly.")
        try:
            cb = driver.find_element(By.ID, _CANCEL_CB)
            driver.execute_script("arguments[0].click();", cb)
            log(f"  [INFO] Clicked #{_CANCEL_CB}.")
        except Exception as exc:
            raise RuntimeError(f"Could not click CANCEL PO checkbox #{_CANCEL_CB}: {exc}")

    time.sleep(0.3)

    # ── Click Save ────────────────────────────────────────────────────────
    log(f"  [INFO] Clicking Save…")
    try:
        save_btn = wait.until(EC.element_to_be_clickable((By.ID, _SAVE_BTN)))
    except TimeoutException:
        raise RuntimeError(f"Save button #{_SAVE_BTN} not found after {_WAIT_SEC}s.")
    save_btn.click()

    # ── Wait to return to PO list ─────────────────────────────────────────
    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        pass  # page may be slow but save already fired
    log(f"  [INFO] Done. Current URL: {driver.current_url}")


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
                _cancel_single_po(driver, log, url, po)
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
