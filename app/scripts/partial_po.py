"""
Partial PO — for each PO number, opens the PO detail and sets every
product line's ORDERED qty to match its SHIPPED qty, then saves.

Enter PO numbers in the PO Numbers box in the sidebar.
No URL or Extra Parameters needed — the site address is built in.

Steps (mirrors the Chrome Recorder recording):
  1. Navigate to http://edw.select-sales.com/PO
  2. Type the PO number into #POKeywordsFilter_I and press Enter
  3. Click the first result row
  4. Read the grid header row to find ORDERED and SHIPPED column indices
  5. For every product row where ORDERED ≠ SHIPPED:
       a. ActionChains-click the ORDERED cell (trusted mousedown/up/click)
       b. Wait for the DXEditor input to appear and JS-focus it
       c. Type new value via switch_to.active_element + ActionChains TAB to commit
  6. Click Save (#EditFormButton_CD)
  7. Reset browser to PO list and repeat for the next PO number
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    ElementNotInteractableException,
)

from utils.browser import make_driver

# ── site constants ────────────────────────────────────────────────────────────
_HOME      = 'http://edw.select-sales.com/'
_PO_LIST   = 'http://edw.select-sales.com/PO'
_SEARCH    = 'POKeywordsFilter_I'
_ROW0_CELL = '#POResults_DXDataRow0 > td:nth-of-type(3)'
_SAVE      = 'EditFormButton_CD'
_WAIT      = 15

# All three Chrome Recorder recordings confirm the ORDERED column is always
# td:nth-of-type(15) — that is index 14 (0-based) among direct-child tds.
# The editor that opens is always #POProducts_DXEditor13_I.
#
# The header row (POProducts_DXHeadersRow0) has 3 header tds per data column
# (name td, name td, blank td) so header indices ≠ data-row indices.
# SHIPPED is always the column immediately after ORDERED:
#   header ORDERED at td[40] → data td[14]   (confirmed by all recordings)
#   header SHIPPED at td[43] → data td[15]   (right after ORDERED)
_ORDERED_COL = 14   # 0-based direct-child td index; CSS nth-of-type is 1-based → 15
_SHIPPED_COL = 15   # always ORDERED + 1
# Dynamic selector — the editor column index varies per grid (e.g. DXEditor8_I,
# DXEditor13_I), so we match any open editor by prefix/suffix pattern.
_EDITOR_SEL  = '[id^="POProducts_DXEditor"][id$="_I"]'


# ── helpers ───────────────────────────────────────────────────────────────────

def _dump_browser_logs(driver, log, label=''):
    """
    Read Chrome browser-console log entries and emit them to the UI log.
    Only WARNING and SEVERE entries are shown to avoid flooding the output.
    console.log()/console.error() calls from the page appear here as well —
    these often reveal DevExpress errors that are invisible in headless mode.
    """
    try:
        entries = driver.get_log('browser')
    except Exception:
        return
    if not entries:
        return
    prefix = f'  [BROWSER]{" "+label if label else ""}'
    for e in entries:
        lvl = e.get('level', 'INFO')
        if lvl not in ('WARNING', 'SEVERE'):
            continue
        msg = e.get('message', '').replace('\n', ' ')[:300]
        log(f"{prefix} [{lvl}] {msg}")


def _try_login(driver, log, email, password):
    """
    Detect the login page and fill in credentials automatically.
    Called right after the initial navigation; a no-op if already logged in.
    """
    # Give the page a moment to settle then check for a password input
    time.sleep(1)
    pwd_inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
    if not pwd_inputs:
        return  # not on login page

    log("  [INFO] Login page detected — filling credentials…")
    try:
        # Find email/username field — try common ASP.NET MVC patterns
        email_field = None
        for sel in [
            'input[name="Email"]', 'input[type="email"]',
            'input[name="UserName"]', 'input[name="username"]',
            '#Email', '#UserName',
        ]:
            matches = driver.find_elements(By.CSS_SELECTOR, sel)
            if matches:
                email_field = matches[0]
                break

        if not email_field:
            raise RuntimeError("Could not find email/username field on login page")

        email_field.clear()
        email_field.send_keys(email)

        pwd_inputs[0].clear()
        pwd_inputs[0].send_keys(password)
        pwd_inputs[0].send_keys(Keys.RETURN)

        # Wait up to 15 s for the login redirect
        WebDriverWait(driver, 15).until(
            lambda d: not d.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
        )
        log(f"  [INFO] Login successful — URL: {driver.current_url}")
    except TimeoutException:
        raise RuntimeError(
            "Login failed — still seeing a password field after 15 s. "
            "Check credentials in the debug panel."
        )


def _go_to_po_list(driver):
    driver.get(_PO_LIST)
    try:
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, _SEARCH))
        )
    except TimeoutException:
        pass


def _click_ordered_cell(driver, row_n, max_attempts=3):
    """
    Click the ORDERED cell using the exact CSS selector from Chrome Recorder:
        #POProducts_DXDataRowN > td:nth-of-type(15)

    The `> td` (direct child) is critical — find_elements(TAG_NAME, 'td')
    returns ALL descendant tds including nested ones, which shifts the index.
    CSS nth-of-type(15) counts only direct-child tds, matching index 14 (0-based).
    """
    # nth-of-type is 1-indexed: index 14 → nth-of-type(15)
    css = f'#POProducts_DXDataRow{row_n} > td:nth-of-type({_ORDERED_COL + 1})'
    for attempt in range(max_attempts):
        try:
            cell = driver.find_element(By.CSS_SELECTOR, css)
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", cell
            )
            ActionChains(driver).click(cell).perform()
            return True
        except StaleElementReferenceException:
            if attempt < max_attempts - 1:
                time.sleep(0.3)
        except NoSuchElementException:
            return False
    return False


def _discover_columns(driver, log):
    """
    Read POProducts_DXHeadersRow0 to find the td indices for ORDERED and SHIPPED.
    Returns (ordered_idx, shipped_idx) or raises RuntimeError.
    """
    result = driver.execute_script("""
        var hdr = document.getElementById('POProducts_DXHeadersRow0');
        if (!hdr) return null;
        var tds = hdr.getElementsByTagName('td');
        var out = {ordered: -1, shipped: -1, headers: []};
        for (var i = 0; i < tds.length; i++) {
            var raw = (tds[i].innerText || tds[i].textContent || '');
            var t   = raw.replace(/[^A-Za-z\\s]/g, '')
                         .replace(/\\s+/g, ' ')
                         .trim()
                         .toUpperCase();
            out.headers.push(i + ':' + t);
            // Use contains-match so "Qty Ordered", "Order Qty", etc. are found
            if (out.ordered < 0 && t.indexOf('ORDER') >= 0) out.ordered = i;
            else if (out.shipped < 0 && t.indexOf('SHIP') >= 0) out.shipped = i;
        }
        return out;
    """)

    if result:
        all_headers = ' | '.join(result.get('headers', []))
        log(f"  [DIAG] Grid headers: {all_headers}")
        o = result.get('ordered', -1)
        s = result.get('shipped', -1)
        if o >= 0 and s >= 0:
            log(f"  [INFO] Column indices — ORDERED: td[{o}], SHIPPED: td[{s}]")
            return o, s
        raise RuntimeError(
            f"Could not find ORDERED/SHIPPED columns. "
            f"ORDERED={'td['+str(o)+']' if o>=0 else 'NOT FOUND'}, "
            f"SHIPPED={'td['+str(s)+']' if s>=0 else 'NOT FOUND'}. "
            f"Headers: {all_headers}"
        )

    raise RuntimeError(
        "POProducts_DXHeadersRow0 not found on the page. "
        f"URL: {driver.current_url}"
    )


def _read_row_values(driver, row_id):
    """
    Return (ordered_text, shipped_text) or (None, None) on error.

    Uses hardcoded _ORDERED_COL=14 and _SHIPPED_COL=15 — both confirmed
    by data-row DIAG:  td[14]=ORDERED, td[15]=SHIPPED, td[16]=OPEN.

    The header row has 3 tds per data column so header indices cannot be
    used directly; both data-column indices are hardcoded here.

    Uses XPath './td' (direct children only) so counts match CSS `> td`.
    The site shows SHIPPED as blank (not '0') when nothing shipped —
    normalised to '0' for comparison.
    """
    try:
        row_el = driver.find_element(By.ID, row_id)
        cells  = row_el.find_elements(By.XPATH, './td')
        if len(cells) <= _SHIPPED_COL:
            return None, None
        ordered = cells[_ORDERED_COL].text.strip()
        shipped = cells[_SHIPPED_COL].text.strip() or '0'
        return ordered, shipped
    except (NoSuchElementException, StaleElementReferenceException):
        return None, None


def _set_editor_value(driver, value, log, row_label):
    """
    Type a value into the open DevExpress inline editor and commit it.
    Returns True only if the editor actually CLOSED (commit confirmed).
    Returns False if the editor is still open after all attempts.

    Commit strategy:
      1. DevExpress JS API: POProducts.UpdateEdit() — direct API call, no
         event handling needed, bypasses isTrusted restrictions entirely.
      2. Enter key sent directly to the editor element.
      3. Click the grid's header row (blur trigger within DevExpress scope).
      4. JS document.activeElement.blur() (force blur).
      5. Tab key sent directly to the editor element.
    """
    wait = WebDriverWait(driver, _WAIT)

    try:
        editor = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, _EDITOR_SEL))
        )
    except TimeoutException:
        log(f"  [WARN] {row_label}: no editor appeared after {_WAIT}s")
        return False

    editor_id = editor.get_attribute('id') or '(no id)'
    log(f"  [DIAG] {row_label}: editor id='{editor_id}'")

    # Click editor for trusted focus, then clear + type
    ActionChains(driver).click(editor).perform()
    time.sleep(0.15)

    active_id = driver.execute_script(
        "return (document.activeElement && document.activeElement.id) || '(no id)'"
    )
    log(f"  [DIAG] {row_label}: active element after click = '{active_id}'")

    editor.send_keys(Keys.CONTROL + 'a')
    editor.send_keys(str(value))
    time.sleep(0.15)

    # Confirm what was actually typed into the input
    try:
        typed_val = editor.get_attribute('value') or ''
        log(f"  [DIAG] {row_label}: input value after typing = '{typed_val}'")
    except Exception:
        pass

    def editor_closed():
        return not driver.find_elements(By.CSS_SELECTOR, _EDITOR_SEL)

    def poll_for_close(label, max_seconds=2.5):
        """Poll up to max_seconds for the editor to disappear. Returns True if closed."""
        deadline = time.time() + max_seconds
        while time.time() < deadline:
            if editor_closed():
                return True
            time.sleep(0.25)
        return False

    # ── Commit attempt 1: DevExpress JS API (UpdateEdit) ──────────────────
    # Calls DevExpress's own client-side API — no event handling, no
    # isTrusted concern.  UpdateEdit() may be async, so poll for 2.5 s.
    try:
        api_result = driver.execute_script("""
            if (window.POProducts &&
                    typeof window.POProducts.UpdateEdit === 'function') {
                window.POProducts.UpdateEdit();
                return 'window.POProducts.UpdateEdit';
            }
            if (typeof ASPx !== 'undefined' && ASPx.GetControlCollection) {
                var cc  = ASPx.GetControlCollection();
                var g   = cc.GetByName ? cc.GetByName('POProducts') : null;
                if (g && g.UpdateEdit) {
                    g.UpdateEdit();
                    return 'ASPx.UpdateEdit';
                }
            }
            return null;
        """)
        if poll_for_close('JS API'):
            log(f"  [DIAG] {row_label}: typed '{value}' — committed via JS API ({api_result})")
            return True
        if api_result:
            log(f"  [DIAG] {row_label}: JS API ({api_result}) called — editor still open after 2.5s")
        else:
            log(f"  [DIAG] {row_label}: JS API not found — POProducts.UpdateEdit unavailable")
    except Exception as exc:
        log(f"  [DIAG] {row_label}: JS API error: {exc}")

    # ── Commit attempt 2: Enter directly on the editor element ────────────
    # ElementNotInteractableException here often means DevExpress is in the
    # middle of closing the editor (UpdateEdit started but isn't done yet).
    # Treat it the same as stale — poll for closure.
    try:
        editor.send_keys(Keys.RETURN)
    except (StaleElementReferenceException, ElementNotInteractableException) as exc:
        log(f"  [DIAG] {row_label}: editor Enter → {type(exc).__name__} (may be mid-close)")
    if poll_for_close('editor Enter'):
        log(f"  [DIAG] {row_label}: typed '{value}' — committed via editor Enter")
        return True
    log(f"  [DIAG] {row_label}: editor Enter did not close editor")

    # ── Commit attempt 3: click the grid header row (blur within grid) ────
    try:
        header = driver.find_element(By.ID, 'POProducts_DXHeadersRow0')
        ActionChains(driver).click(header).perform()
        if poll_for_close('header click'):
            log(f"  [DIAG] {row_label}: typed '{value}' — committed via header-row click")
            return True
        log(f"  [DIAG] {row_label}: header-row click did not close editor")
    except NoSuchElementException:
        log(f"  [DIAG] {row_label}: POProducts_DXHeadersRow0 not found")

    # ── Commit attempt 4: JS blur on active element ────────────────────────
    driver.execute_script(
        "if (document.activeElement) document.activeElement.blur();"
    )
    if poll_for_close('JS blur'):
        log(f"  [DIAG] {row_label}: typed '{value}' — committed via JS blur")
        return True
    log(f"  [DIAG] {row_label}: JS blur did not close editor")

    # ── Commit attempt 5: Tab directly on the editor element ──────────────
    try:
        editor.send_keys(Keys.TAB)
    except (StaleElementReferenceException, ElementNotInteractableException) as exc:
        log(f"  [DIAG] {row_label}: Tab → {type(exc).__name__} (may be mid-close)")
    if poll_for_close('Tab'):
        log(f"  [DIAG] {row_label}: typed '{value}' — committed via Tab")
        return True
    log(f"  [DIAG] {row_label}: Tab did not close editor")

    log(f"  [WARN] {row_label}: editor STILL OPEN after all 5 commit attempts — value NOT saved")
    return False


# ── core PO logic ─────────────────────────────────────────────────────────────

def _partial_single_po(driver, log, po,
                        debug_email=None, debug_password=None,
                        capture_logs=False):
    wait = WebDriverWait(driver, _WAIT)

    # ── Navigate to PO list (auto-login if credentials supplied) ──────────
    _go_to_po_list(driver)
    if debug_email and debug_password:
        _try_login(driver, log, debug_email, debug_password)
    if capture_logs:
        _dump_browser_logs(driver, log, 'after nav to PO list')

    try:
        search = wait.until(EC.presence_of_element_located((By.ID, _SEARCH)))
    except TimeoutException:
        raise RuntimeError(
            f"#{_SEARCH} not found after {_WAIT}s "
            f"(current URL: {driver.current_url}). "
            "Session may have expired — re-paste cookies and retry."
        )

    search.clear()
    search.send_keys(po)
    log(f"  [INFO] Typed {po} — pressing Enter…")
    search.send_keys(Keys.RETURN)

    # ── Click first result row ─────────────────────────────────────────────
    # DevExpress sometimes re-renders the results grid after the search
    # completes, which invalidates the element reference.  Retry up to 3
    # times, re-fetching the element each attempt.
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, _ROW0_CELL)))
    except TimeoutException:
        raise RuntimeError(f"PO {po} did not appear in results after {_WAIT}s.")

    log(f"  [INFO] Found result — opening PO detail…")
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

    try:
        wait.until(EC.url_contains('/PO/Edit/'))
    except TimeoutException:
        raise RuntimeError(
            f"Detail page did not load after {_WAIT}s "
            f"(URL: {driver.current_url})."
        )
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Wait for product grid with real data ───────────────────────────────
    try:
        wait.until(
            EC.presence_of_element_located((By.ID, 'POProducts_DXHeadersRow0'))
        )
        wait.until(lambda d: bool(d.execute_script("""
            var hdr = document.getElementById('POProducts_DXHeadersRow0');
            var row = document.getElementById('POProducts_DXDataRow0');
            if (!hdr || !row) return false;
            var cells = row.querySelectorAll('td');
            for (var i = 0; i < cells.length; i++) {
                if (cells[i].textContent.trim().startsWith('$')) return true;
            }
            return false;
        """)))
    except TimeoutException:
        log(f"  [INFO] No product rows found — nothing to adjust.")
        return

    # ── Log headers for diagnostics (not used for indexing) ───────────────
    # The header row has 3 tds per data column so its indices ≠ data indices.
    # We log it purely so the DIAG output can confirm which columns exist.
    try:
        _discover_columns(driver, log)
    except RuntimeError as e:
        log(f"  [WARN] Header scan: {e}")
    if capture_logs:
        _dump_browser_logs(driver, log, 'after header scan')

    # ── Diagnostic: confirm data-row values at the hardcoded indices ───────
    try:
        diag = driver.execute_script("""
            var row = document.getElementById('POProducts_DXDataRow0');
            if (!row) return null;
            var cells = Array.from(row.children).filter(n => n.tagName === 'TD');
            var out = [];
            for (var i = 12; i < Math.min(18, cells.length); i++) {
                out.push(i + ':' + cells[i].textContent.trim());
            }
            return out.join(' | ');
        """)
        if diag:
            log(f"  [DIAG] Row 0 data tds[12-17]: {diag}")
        log(f"  [INFO] Using hardcoded ORDERED=td[{_ORDERED_COL}], SHIPPED=td[{_SHIPPED_COL}]")
    except Exception:
        pass

    # ── Pass 1: read all row values ────────────────────────────────────────
    rows_to_edit = []
    row_n = 0

    while True:
        row_id = f'POProducts_DXDataRow{row_n}'
        ordered, shipped = _read_row_values(driver, row_id)

        if ordered is None and shipped is None:
            if not driver.find_elements(By.ID, row_id):
                break
            row_n += 1
            continue

        if ordered == shipped:
            log(f"  [OK]   Row {row_n+1}: ORDERED={ordered} already matches SHIPPED={shipped}")
        else:
            log(f"  [EDIT] Row {row_n+1}: ORDERED {ordered} → {shipped}")
            rows_to_edit.append((row_n, row_id, shipped))

        row_n += 1

    if not rows_to_edit:
        log(f"  [INFO] All rows already match — nothing to save.")
        return

    # ── Pass 2: click td:nth-of-type(15), type value, commit ─────────────
    # Uses the exact CSS selector from all three Chrome Recorder recordings:
    #   #POProducts_DXDataRowN > td:nth-of-type(15)
    # The `>` (direct child) is what makes the index reliable.
    changes = 0
    failed_rows = []
    for row_n, row_id, shipped_text in rows_to_edit:
        label = f"Row {row_n+1}"

        clicked = _click_ordered_cell(driver, row_n)
        if not clicked:
            log(f"  [WARN] {label}: could not find/click #POProducts_DXDataRow{row_n} > td:nth-of-type({_ORDERED_COL + 1})")
            failed_rows.append(label)
            continue

        time.sleep(0.4)

        editors_now = driver.find_elements(By.CSS_SELECTOR, _EDITOR_SEL)
        if editors_now:
            log(f"  [DIAG] {label}: editor OPEN — id={editors_now[0].get_attribute('id')}")
        else:
            log(f"  [DIAG] {label}: editor NOT OPEN — td:nth-of-type({_ORDERED_COL + 1}) may be wrong column")
            failed_rows.append(label)
            continue

        committed = _set_editor_value(driver, shipped_text, log, label)

        # Verify cell value changed (empty = editor still open)
        time.sleep(0.3)
        try:
            new_val = driver.find_element(
                By.CSS_SELECTOR,
                f'#POProducts_DXDataRow{row_n} > td:nth-of-type({_ORDERED_COL + 1})'
            ).text.strip()
            log(f"  [DIAG] {label}: cell value after commit = '{new_val}' (expected '{shipped_text}')")
            if committed and new_val == shipped_text:
                changes += 1
            else:
                log(f"  [WARN] {label}: commit did not change cell value — row NOT counted")
                failed_rows.append(label)
        except Exception:
            if committed:
                changes += 1  # can't verify but commit said it worked

    if capture_logs:
        _dump_browser_logs(driver, log, 'after all edits')

    if changes == 0:
        raise RuntimeError(
            f"Could not commit any edits — {len(failed_rows)} row(s) failed. "
            "The editor refused to close after all commit attempts. "
            "Check [DIAG] lines above for which commit method was attempted."
        )

    # ── Click Save ─────────────────────────────────────────────────────────
    # Chrome Recorder: click the <span> inside #EditFormButton_CD, not the
    # outer div.  The span is the actual clickable label.
    log(f"  [INFO] {changes} row(s) updated — clicking Save…")

    save_method = None
    try:
        save_span = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f'#{_SAVE} span'))
        )
        ActionChains(driver).click(save_span).perform()
        save_method = 'span'
    except (TimeoutException, NoSuchElementException,
            StaleElementReferenceException, ElementNotInteractableException):
        pass

    if save_method is None:
        # Fall back to the outer button element
        try:
            save_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, _SAVE))
            )
            ActionChains(driver).click(save_btn).perform()
            save_method = 'button'
        except (TimeoutException, NoSuchElementException,
                StaleElementReferenceException, ElementNotInteractableException):
            pass

    if save_method is None:
        raise RuntimeError(
            f"Could not find or click the Save button (#{_SAVE} span). "
            f"URL: {driver.current_url}"
        )

    log(f"  [INFO] Save clicked (method: {save_method}).")
    if capture_logs:
        _dump_browser_logs(driver, log, 'after Save click')

    # Verify save went through — page must navigate away from /PO/Edit/
    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        if capture_logs:
            _dump_browser_logs(driver, log, 'save timeout — page did not navigate')
        raise RuntimeError(
            "Save was clicked but the page is still on the edit URL after "
            f"{_WAIT}s. Possible causes: validation error, editor still open, "
            f"or session expired. URL: {driver.current_url}"
        )
    log(f"  [INFO] Saved. URL: {driver.current_url}")

    # ── Post-save verification ─────────────────────────────────────────────
    # Re-open the PO and confirm every row that was supposed to change now
    # shows ORDERED == SHIPPED.  A save that silently fails (e.g. validation
    # error, editor still open) will show the old values here.
    log(f"  [INFO] Re-opening PO to verify edits persisted…")
    _go_to_po_list(driver)
    try:
        vsearch = WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, _SEARCH))
        )
        vsearch.clear()
        vsearch.send_keys(po)
        vsearch.send_keys(Keys.RETURN)
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _ROW0_CELL))
        )
        row_cell = driver.find_element(By.CSS_SELECTOR, _ROW0_CELL)
        ActionChains(driver).click(row_cell).perform()
        WebDriverWait(driver, _WAIT).until(EC.url_contains('/PO/Edit/'))
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, 'POProducts_DXHeadersRow0'))
        )
        time.sleep(0.5)

        verify_failures = []
        for row_n, row_id, expected_val in rows_to_edit:
            ordered, shipped = _read_row_values(driver, row_id)
            if ordered is None:
                continue
            if ordered == expected_val:
                log(f"  [VERIFY] Row {row_n+1}: ORDERED={ordered} ✓")
            else:
                log(f"  [VERIFY] Row {row_n+1}: ORDERED={ordered} — expected {expected_val} ✗")
                verify_failures.append(f"Row {row_n+1}")

        if verify_failures:
            raise RuntimeError(
                f"Save appeared to succeed but {len(verify_failures)} row(s) "
                f"still show wrong ORDERED value: {', '.join(verify_failures)}. "
                "The value change was NOT persisted — editor may not have committed "
                "before Save was clicked."
            )
        log(f"  [INFO] Verification passed — all {len(rows_to_edit)} edit(s) confirmed.")
    except RuntimeError:
        raise
    except Exception as ve:
        log(f"  [WARN] Verification step failed: {ve} — could not confirm edits")


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

    # Debug mode: check "Show browser window" checkbox in the UI.
    # When active, the browser opens visibly, auto-logs in with debug
    # credentials (if supplied), and streams Chrome console entries to the log.
    debug_mode    = str(params.get('show_browser', '')).lower() in ('true', '1', 'yes')
    debug_email   = params.get('debug_email')    or None
    debug_password= params.get('debug_password') or None
    headless      = not debug_mode

    log(f"[INFO] Starting browser {'(headless)' if headless else '(VISIBLE — debug mode)'}…")
    if debug_mode and debug_email:
        log(f"[INFO] Debug credentials: {debug_email} / {'*' * len(debug_password or '')}")
    try:
        driver = make_driver(
            cookies=cookies,
            headless=headless,
            initial_url=_HOME,
            enable_logging=debug_mode,
        )
    except Exception as exc:
        log(f"[ERROR] Browser failed to start: {exc}")
        return

    success_count = 0
    fail_count    = 0
    try:
        for i, po in enumerate(po_numbers, 1):
            log(f"[INFO] ({i}/{len(po_numbers)}) Processing PO: {po}")
            try:
                _partial_single_po(
                    driver, log, po,
                    debug_email=debug_email,
                    debug_password=debug_password,
                    capture_logs=debug_mode,
                )
                success_count += 1
                log(f"  [SUCCESS] PO {po} done.")
            except Exception as exc:
                fail_count += 1
                log(f"  [ERROR] PO {po} — {exc}")
            finally:
                try:
                    if '/PO/Edit/' in driver.current_url:
                        log(f"  [INFO] Resetting browser to PO list…")
                        _go_to_po_list(driver)
                except Exception:
                    pass
            log("─" * 60)

        log("[INFO] All done — resetting to PO list.")
        _go_to_po_list(driver)

    finally:
        driver.quit()
        log("[INFO] Browser closed.")

    log(f"[RESULT] Finished — {success_count} done, {fail_count} failed.")
