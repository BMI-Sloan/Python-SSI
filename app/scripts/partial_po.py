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
       a. Click the ORDERED cell
       b. Wait for any DXEditor input to appear
       c. Set value to SHIPPED qty, blur to commit
  6. Click Save (#EditFormButton_CD)
  7. Reset browser to PO list and repeat for the next PO number
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

# Matches ANY open DX inline editor in the products grid regardless of column index
# DevExpress IDs follow the pattern: POProducts_DXEditor{col}_I
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
    """Click a grid cell; falls back to JS click if shadow DOM blocks it."""
    for attempt in range(max_attempts):
        try:
            row_el = driver.find_element(By.ID, row_id)
            cells  = row_el.find_elements(By.TAG_NAME, 'td')
            cell   = cells[cell_idx]
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", cell
            )
            try:
                cell.click()
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
    Logs every header it finds so we can debug column layout.
    Returns (ordered_idx, shipped_idx) or raises RuntimeError.
    """
    result = driver.execute_script("""
        var hdr = document.getElementById('POProducts_DXHeadersRow0');
        if (!hdr) return null;
        var tds = hdr.getElementsByTagName('td');
        var out = {ordered: -1, shipped: -1, headers: []};
        for (var i = 0; i < tds.length; i++) {
            // innerText respects CSS visibility; strip non-alpha chars (sort arrows, etc.)
            var raw = (tds[i].innerText || tds[i].textContent || '');
            var t   = raw.replace(/[^A-Za-z\\s]/g, '')
                         .replace(/\\s+/g, ' ')
                         .trim()
                         .toUpperCase();
            out.headers.push(i + ':' + t);
            if (t === 'ORDERED')       out.ordered  = i;
            else if (t === 'SHIPPED')  out.shipped  = i;
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
    """Return (ordered_text, shipped_text) for a row, or (None, None) on error."""
    try:
        row_el = driver.find_element(By.ID, row_id)
        cells  = row_el.find_elements(By.TAG_NAME, 'td')
        if len(cells) <= max(ordered_idx, shipped_idx):
            return None, None
        return cells[ordered_idx].text.strip(), cells[shipped_idx].text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return None, None


def _set_editor_value(driver, value, log, row_label):
    """
    Set the value in the currently open DX inline editor.
    Tries Selenium send_keys first; falls back to JS for Shadow DOM.
    Returns True on success.
    """
    wait = WebDriverWait(driver, _WAIT)

    # ── Selenium path ─────────────────────────────────────────────────────
    try:
        editor = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, _EDITOR_SEL))
        )
        editor.send_keys(Keys.CONTROL + 'a')
        editor.send_keys(str(value))
        # Blur via JS to trigger DevExpress change handler without pressing Enter
        # (pressing Enter on the last row can submit the form prematurely)
        driver.execute_script("document.activeElement && document.activeElement.blur();")
        return True
    except (ElementNotInteractableException, TimeoutException,
            StaleElementReferenceException):
        log(f"  [WARN] {row_label}: send_keys blocked — falling back to JS")

    # ── JS path ───────────────────────────────────────────────────────────
    result = driver.execute_script("""
        var val = arguments[0];

        // Strategy 1 — DevExpress ASPxClientControl API
        try {
            var coll = ASPxClientControl.GetControlCollection();
            var all  = coll.GetControls ? coll.GetControls() : [];
            for (var i = 0; i < all.length; i++) {
                var e = all[i];
                if (e.name && e.name.indexOf('POProducts_DXEditor') === 0
                        && typeof e.SetValue === 'function') {
                    e.SetValue(val);
                    if (typeof e.GetMainElement === 'function')
                        e.GetMainElement().blur();
                    return 'dxapi:' + e.name;
                }
            }
        } catch(ex) {}

        // Strategy 2 — querySelector in regular DOM
        var inp = document.querySelector(
            '[id^="POProducts_DXEditor"][id$="_I"]'
        );
        if (inp) {
            var s = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
            s.call(inp, val);
            inp.dispatchEvent(new Event('input',  {bubbles: true}));
            inp.dispatchEvent(new Event('change', {bubbles: true}));
            inp.blur();
            return 'css:' + inp.id;
        }

        // Strategy 3 — deep Shadow DOM search
        function deepQ(root, sel) {
            var el = root.querySelector ? root.querySelector(sel) : null;
            if (el) return el;
            var nodes = root.querySelectorAll
                ? Array.from(root.querySelectorAll('*')) : [];
            for (var n of nodes) {
                if (n.shadowRoot) {
                    var f = deepQ(n.shadowRoot, sel);
                    if (f) return f;
                }
            }
            return null;
        }
        var inp2 = deepQ(document,
            '[id^="POProducts_DXEditor"][id$="_I"]');
        if (inp2) {
            var s2 = Object.getOwnPropertyDescriptor(
                         window.HTMLInputElement.prototype, 'value').set;
            s2.call(inp2, val);
            inp2.dispatchEvent(new Event('input',  {bubbles: true}));
            inp2.dispatchEvent(new Event('change', {bubbles: true}));
            inp2.blur();
            return 'shadow:' + inp2.id;
        }

        return 'not_found';
    """, str(value))

    if result == 'not_found':
        log(f"  [WARN] {row_label}: JS could not find the editor input either")
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
    try:
        row_cell = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _ROW0_CELL))
        )
    except TimeoutException:
        raise RuntimeError(f"PO {po} did not appear in results after {_WAIT}s.")

    log(f"  [INFO] Found result — opening PO detail…")
    row_cell.click()

    try:
        wait.until(EC.url_contains('/PO/Edit/'))
    except TimeoutException:
        raise RuntimeError(
            f"Detail page did not load after {_WAIT}s "
            f"(URL: {driver.current_url})."
        )
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Wait for product grid with real data ───────────────────────────────
    # We wait until the grid HEADER row exists AND a data row contains a
    # dollar-value cell — that confirms DevExpress finished loading the new
    # PO's products (not the previous page's stale DOM).
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

        if not shipped:
            log(f"  [SKIP] Row {row_n+1}: SHIPPED is blank")
        elif ordered == shipped:
            log(f"  [OK]   Row {row_n+1}: ORDERED={ordered} already matches SHIPPED={shipped}")
        else:
            log(f"  [EDIT] Row {row_n+1}: ORDERED {ordered} → {shipped}")
            rows_to_edit.append((row_n, row_id, shipped))

        row_n += 1

    if not rows_to_edit:
        log(f"  [INFO] All rows already match — nothing to save.")
        return

    # ── Pass 2: click and edit each mismatched cell ────────────────────────
    changes = 0
    for row_n, row_id, shipped_text in rows_to_edit:

        clicked = _click_cell(driver, row_id, ordered_idx)
        if not clicked:
            log(f"  [WARN] Row {row_n+1}: could not click ORDERED cell — skipping")
            continue

        time.sleep(0.5)   # give DevExpress time to open the inline editor

        ok = _set_editor_value(driver, shipped_text, log, f"Row {row_n+1}")
        if not ok:
            # Dismiss any open editor so next iteration starts clean
            try:
                driver.execute_script(
                    "var e = document.querySelector(arguments[0]);"
                    "if (e) e.blur();",
                    _EDITOR_SEL
                )
            except Exception:
                pass
            continue

        time.sleep(0.3)   # give DevExpress time to commit the value
        changes += 1

    if changes == 0:
        log(f"  [INFO] No edits succeeded — skipping Save.")
        return

    # ── Click Save ─────────────────────────────────────────────────────────
    log(f"  [INFO] {changes} row(s) updated — clicking Save…")
    save_result = driver.execute_script("""
        // Strategy 1 — direct getElementById
        var el = document.getElementById('EditFormButton_CD');
        if (el) {
            el.scrollIntoView({block: 'center'});
            el.click();
            return 'id';
        }

        // Strategy 2 — deep shadow DOM search
        function deepFind(root) {
            var found = root.querySelector
                ? root.querySelector('#EditFormButton_CD') : null;
            if (found) return found;
            var nodes = root.querySelectorAll
                ? Array.from(root.querySelectorAll('*')) : [];
            for (var n of nodes) {
                if (n.shadowRoot) {
                    var r = deepFind(n.shadowRoot);
                    if (r) return r;
                }
            }
            return null;
        }
        var el2 = deepFind(document);
        if (el2) {
            el2.scrollIntoView({block: 'center'});
            el2.click();
            return 'shadow';
        }

        // Strategy 3 — match by visible "Save" text
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
    log(f"  [INFO] Save clicked (method: {save_result}).")

    try:
        wait.until(lambda d: '/PO/Edit/' not in d.current_url)
    except TimeoutException:
        pass
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
