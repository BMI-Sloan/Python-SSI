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

# Matches ANY open DX inline editor regardless of column index
# DevExpress IDs: POProducts_DXEditor{col}_I
_EDITOR_SEL = '[id^="POProducts_DXEditor"][id$="_I"]'


# ── helpers ───────────────────────────────────────────────────────────────────

def _go_to_po_list(driver):
    driver.get(_PO_LIST)
    try:
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, _SEARCH))
        )
    except TimeoutException:
        pass


def _click_cell(driver, row_id, cell_idx, max_attempts=3):
    """
    Click a grid cell using ActionChains (trusted mousedown/mouseup/click).
    DevExpress checks event.isTrusted before opening the inline editor;
    plain el.click() and JS dispatchEvent are untrusted and may be ignored.
    Falls back to JS click only if ActionChains raises ElementNotInteractable.
    """
    for attempt in range(max_attempts):
        try:
            row_el = driver.find_element(By.ID, row_id)
            cells  = row_el.find_elements(By.TAG_NAME, 'td')
            cell   = cells[cell_idx]
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", cell
            )
            try:
                ActionChains(driver).click(cell).perform()
            except ElementNotInteractableException:
                driver.execute_script("arguments[0].click();", cell)
            return True
        except StaleElementReferenceException:
            if attempt < max_attempts - 1:
                time.sleep(0.3)
        except (NoSuchElementException, IndexError):
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
            if (t === 'ORDERED')       out.ordered = i;
            else if (t === 'SHIPPED')  out.shipped = i;
        }
        return out;
    """)

    if result:
        log(f"  [DIAG] Grid headers: {' | '.join(result.get('headers', []))}")
        o = result.get('ordered', -1)
        s = result.get('shipped', -1)
        if o >= 0 and s >= 0:
            log(f"  [INFO] Column indices — ORDERED: td[{o}], SHIPPED: td[{s}]")
            return o, s

    raise RuntimeError(
        "Could not find ORDERED and SHIPPED headers in POProducts grid. "
        f"Discovery returned: {result}"
    )


def _read_row_values(driver, row_id, ordered_idx, shipped_idx):
    """
    Return (ordered_text, shipped_text) or (None, None) on error.
    The site leaves SHIPPED blank instead of '0' when nothing was shipped —
    normalise blank to '0' so the comparison works correctly.
    """
    try:
        row_el = driver.find_element(By.ID, row_id)
        cells  = row_el.find_elements(By.TAG_NAME, 'td')
        if len(cells) <= max(ordered_idx, shipped_idx):
            return None, None
        ordered = cells[ordered_idx].text.strip()
        shipped = cells[shipped_idx].text.strip() or '0'
        return ordered, shipped
    except (NoSuchElementException, StaleElementReferenceException):
        return None, None


def _set_editor_value(driver, value, log, row_label):
    """
    Set the value in the currently open DX inline editor.

    DevExpress ignores untrusted events (isTrusted=false), so we must use
    WebDriver's native interaction APIs:
      1. JS focus the editor so it becomes document.activeElement
      2. switch_to.active_element.send_keys() — generates trusted key events
      3. ActionChains.send_keys(TAB) — trusted TAB commits the value via
         DevExpress's blur/change handler
    Falls back to DevExpress JS API if the active-element approach fails.
    """
    wait = WebDriverWait(driver, _WAIT)

    # Wait for the editor input to exist in the DOM
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, _EDITOR_SEL)))
    except TimeoutException:
        log(f"  [WARN] {row_label}: no editor appeared after {_WAIT}s")
        return False

    # JS: click + focus the editor so it is the browser's active element.
    # We can't use Selenium .click() here (might fail for Shadow DOM inputs),
    # but JS focus() reliably places focus even inside Shadow DOM.
    driver.execute_script("""
        var ed = document.querySelector(arguments[0]);
        if (ed) { ed.click(); ed.focus(); ed.select(); }
    """, _EDITOR_SEL)
    time.sleep(0.15)

    # switch_to.active_element always returns the focused element — even when
    # that element lives inside a Shadow DOM.  send_keys() on it generates
    # trusted keyboard events that DevExpress's change handler will accept.
    try:
        active = driver.switch_to.active_element
        active_id  = active.get_attribute('id')  or '(no id)'
        active_tag = active.tag_name
        log(f"  [DIAG] {row_label}: active element = <{active_tag} id='{active_id}'>")
        active.send_keys(Keys.CONTROL + 'a')   # select all existing text
        active.send_keys(str(value))            # type new value
        # TAB via ActionChains = trusted blur/change on the editor input,
        # which is what DevExpress requires to persist the value.
        ActionChains(driver).send_keys(Keys.TAB).perform()
        log(f"  [DIAG] {row_label}: typed '{value}' + TAB — checking editor value…")
        # Brief pause then log what value the editor now shows (before commit)
        time.sleep(0.1)
        post_val = driver.execute_script(
            "var e = document.querySelector(arguments[0]); return e ? e.value : null;",
            _EDITOR_SEL
        )
        log(f"  [DIAG] {row_label}: editor value after typing = {post_val!r}")
        return True
    except Exception as e:
        log(f"  [WARN] {row_label}: trusted typing failed ({type(e).__name__}): {e}")

    # Last resort — DevExpress JS API (SetValue on the control object)
    result = driver.execute_script("""
        var val = arguments[0];
        try {
            var coll = ASPxClientControl.GetControlCollection();
            var all  = coll.GetControls ? coll.GetControls() : [];
            for (var i = 0; i < all.length; i++) {
                var e = all[i];
                if (e.name && e.name.indexOf('POProducts_DXEditor') === 0
                        && typeof e.SetValue === 'function') {
                    e.SetValue(val);
                    return 'dxapi:' + e.name;
                }
            }
        } catch(ex) {}
        return 'not_found';
    """, str(value))

    if result == 'not_found':
        log(f"  [WARN] {row_label}: DevExpress API fallback also failed")
        return False

    log(f"  [INFO] {row_label}: value set via {result}")
    return True


# ── core PO logic ─────────────────────────────────────────────────────────────

def _partial_single_po(driver, log, po):
    wait = WebDriverWait(driver, _WAIT)

    # ── Navigate to PO list and search ────────────────────────────────────
    _go_to_po_list(driver)

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

    # ── Discover column indices from header ────────────────────────────────
    ordered_idx, shipped_idx = _discover_columns(driver, log)

    # ── Diagnostic: show data values around discovered columns ─────────────
    try:
        start = max(0, ordered_idx - 2)
        end   = shipped_idx + 3
        diag  = driver.execute_script("""
            var start = arguments[0], end = arguments[1];
            var row = document.getElementById('POProducts_DXDataRow0');
            if (!row) return null;
            var cells = row.querySelectorAll('td');
            var out = [];
            for (var i = start; i < Math.min(end, cells.length); i++) {
                out.push(i + ':' + cells[i].textContent.trim());
            }
            return out.join(' | ');
        """, start, end)
        if diag:
            log(f"  [DIAG] Row 0 td[{start}-{end-1}]: {diag}")
    except Exception:
        pass

    # ── Pass 1: read all row values (no element refs kept) ─────────────────
    rows_to_edit = []
    row_n = 0

    while True:
        row_id = f'POProducts_DXDataRow{row_n}'
        ordered, shipped = _read_row_values(driver, row_id, ordered_idx, shipped_idx)

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

    # ── Edit values via DevExpress JavaScript API ──────────────────────────
    # Clicking cells + simulating keystrokes has been unreliable because
    # DevExpress may check event.isTrusted or require specific focus state.
    # Using the grid's own JS API bypasses all of that entirely.
    api_payload = [
        {'rowIndex': row_n, 'value': int(shipped_text) if shipped_text.isdigit() else 0}
        for row_n, row_id, shipped_text in rows_to_edit
    ]

    api_result = driver.execute_script("""
        var rows = arguments[0];   // [{rowIndex: N, value: V}, ...]
        try {
            var coll = ASPxClientControl.GetControlCollection();
            var controls = coll.GetControls ? coll.GetControls() : [];
            var grid = null;

            // Find the POProducts grid control
            for (var i = 0; i < controls.length; i++) {
                var c = controls[i];
                if (c.name && c.name.indexOf('POProducts') >= 0
                        && typeof c.GetColumnCount === 'function') {
                    grid = c;
                    break;
                }
            }
            if (!grid) return {status: 'no_grid'};

            // Find the ORDERED column index within DevExpress
            // (DevExpress column index != DOM td index)
            var dxColIdx  = -1;
            var fieldName = null;
            var allCols   = [];
            for (var j = 0; j < grid.GetColumnCount(); j++) {
                var col = grid.GetColumn(j);
                var hdr = (col.headerCaption || col.name || '')
                              .replace(/[^A-Za-z\\s]/g, '')
                              .trim().toUpperCase();
                allCols.push(j + ':' + hdr + '(' + (col.fieldName||col.name) + ')');
                if (hdr === 'ORDERED') {
                    dxColIdx  = j;
                    fieldName = col.fieldName || col.name;
                }
            }
            if (dxColIdx < 0) return {
                status: 'no_ordered_col',
                cols: allCols.join(', ')
            };

            var applied = 0;

            // ── Batch edit mode ──────────────────────────────────────────
            if (grid.batchEditApi) {
                for (var k = 0; k < rows.length; k++) {
                    grid.batchEditApi.SetCellValue(
                        rows[k].rowIndex, fieldName, rows[k].value
                    );
                    applied++;
                }
                return {
                    status: 'ok', method: 'batch',
                    field: fieldName, applied: applied
                };
            }

            // ── Cell (inline) edit mode ──────────────────────────────────
            if (typeof grid.StartEdit === 'function') {
                for (var k = 0; k < rows.length; k++) {
                    grid.StartEdit(rows[k].rowIndex);
                    var editor = grid.GetEditor(dxColIdx);
                    if (editor && typeof editor.SetValue === 'function') {
                        editor.SetValue(rows[k].value);
                        applied++;
                    }
                    if (typeof grid.UpdateEdit === 'function') {
                        grid.UpdateEdit();
                    }
                }
                return {
                    status: 'ok', method: 'cell',
                    field: fieldName, applied: applied
                };
            }

            return {status: 'no_edit_api', gridName: grid.name};

        } catch(e) {
            return {status: 'error', msg: e.toString()};
        }
    """, api_payload)

    log(f"  [DIAG] DevExpress API result: {api_result}")

    if isinstance(api_result, dict) and api_result.get('status') == 'ok':
        changes = api_result.get('applied', 0)
        log(f"  [INFO] {changes} value(s) set via DevExpress "
            f"{api_result.get('method')} API (field={api_result.get('field')!r})")
    else:
        # ── Fallback: click/type simulation ───────────────────────────────
        log(f"  [WARN] DevExpress API unavailable — falling back to click/type simulation")
        changes = 0
        for row_n, row_id, shipped_text in rows_to_edit:

            clicked = _click_cell(driver, row_id, ordered_idx)
            if not clicked:
                log(f"  [WARN] Row {row_n+1}: could not click ORDERED cell — skipping")
                continue

            time.sleep(0.5)

            editors_now = driver.find_elements(By.CSS_SELECTOR, _EDITOR_SEL)
            if editors_now:
                log(f"  [DIAG] Row {row_n+1}: editor OPEN — id={editors_now[0].get_attribute('id')}")
            else:
                log(f"  [DIAG] Row {row_n+1}: editor NOT OPEN after click")

            ok = _set_editor_value(driver, shipped_text, log, f"Row {row_n+1}")
            if not ok:
                continue

            time.sleep(0.3)
            changes += 1

    if changes == 0:
        log(f"  [INFO] No edits succeeded — skipping Save.")
        return

    # ── Ensure last editor is fully committed before saving ────────────────
    # TAB in _set_editor_value moved focus away from the last edited cell.
    # Give DevExpress a moment to process the blur/change, then verify no
    # editor input is still open.
    time.sleep(0.5)
    still_open = driver.find_elements(By.CSS_SELECTOR, _EDITOR_SEL)
    if still_open:
        log(f"  [WARN] Editor still visible before Save — sending Escape to close")
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.3)

    # ── Click Save ─────────────────────────────────────────────────────────
    log(f"  [INFO] {changes} row(s) updated — clicking Save…")

    save_method = None
    try:
        save_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, _SAVE))
        )
        ActionChains(driver).click(save_btn).perform()
        save_method = 'selenium'
    except (ElementNotInteractableException, TimeoutException,
            StaleElementReferenceException):
        pass

    if save_method is None:
        save_result = driver.execute_script("""
            var el = document.getElementById('EditFormButton_CD');
            if (el) {
                el.scrollIntoView({block: 'center'});
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
                el.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true}));
                el.click();
                return 'id';
            }
            function deepFind(root) {
                var found = root.querySelector
                    ? root.querySelector('#EditFormButton_CD') : null;
                if (found) return found;
                var nodes = root.querySelectorAll
                    ? Array.from(root.querySelectorAll('*')) : [];
                for (var n of nodes) {
                    if (n.shadowRoot) { var r = deepFind(n.shadowRoot); if (r) return r; }
                }
                return null;
            }
            var el2 = deepFind(document);
            if (el2) {
                el2.scrollIntoView({block: 'center'});
                el2.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true}));
                el2.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true, cancelable:true}));
                el2.click();
                return 'shadow';
            }
            var hits = Array.from(document.querySelectorAll(
                'a, button, input[type=button], input[type=submit]'
            ));
            for (var c of hits) {
                if ((c.textContent || c.value || '').trim().toLowerCase() === 'save') {
                    c.scrollIntoView({block: 'center'});
                    c.click();
                    return 'text';
                }
            }
            return 'not_found';
        """)
        if save_result == 'not_found':
            raise RuntimeError(
                f"Could not find Save button (#{_SAVE}) — tried getElementById, "
                "shadow DOM search, and text search."
            )
        save_method = save_result

    log(f"  [INFO] Save clicked (method: {save_method}).")

    # Verify save went through — page must navigate away from /PO/Edit/
    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        raise RuntimeError(
            "Save was clicked but the page is still on the edit URL after "
            f"{_WAIT}s. Possible causes: validation error, editor still open, "
            f"or session expired. URL: {driver.current_url}"
        )
    log(f"  [INFO] Saved. URL: {driver.current_url}")


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

    # Set show_browser=true in Extra Parameters to watch the browser live.
    # Useful for debugging when edits appear to succeed but nothing changes.
    headless = not str(params.get('show_browser', '')).lower() in ('true', '1', 'yes')
    log(f"[INFO] Starting browser {'(headless)' if headless else '(VISIBLE — debug mode)'}…")
    try:
        driver = make_driver(cookies=cookies, headless=headless, initial_url=_HOME)
    except Exception as exc:
        log(f"[ERROR] Browser failed to start: {exc}")
        return

    success_count = 0
    fail_count    = 0
    try:
        for i, po in enumerate(po_numbers, 1):
            log(f"[INFO] ({i}/{len(po_numbers)}) Processing PO: {po}")
            try:
                _partial_single_po(driver, log, po)
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
