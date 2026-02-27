"""
Partial PO — for each PO number, opens the detail page and sets every
product line's ORDERED quantity to match its SHIPPED quantity, then saves.

Workflow (mirrors cancel_po):
  1. Navigate to the PO search page URL
  2. Search for the PO number in the 'search for' box
  3. Click the PO in the results grid to open the detail page
  4. Find the PRODUCTS table; for each line where ORDERED ≠ SHIPPED:
       - Click into the ORDERED cell to enter edit mode
       - Replace the value with the SHIPPED amount
  5. Click Save and move to the next PO

Extra Parameters (URL box + optional overrides):
    https://your-selectsales-url/path/to/po-search

    Optional JSON overrides (if auto-detection fails):
    {
        "url": "https://...",            // can also be set via URL box
        "search_input_id": "...",
        "save_button_id":  "..."
    }
"""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    ElementNotInteractableException,
    StaleElementReferenceException,
)

from utils.browser import make_driver, wait_for_page


# ── shared element finders (same strategy as cancel_po) ──────────────────────

def _first_visible(elements):
    return next((e for e in elements if e.is_displayed()), None)


def _by_id(driver, eid):
    if not eid:
        return None
    return _first_visible(driver.find_elements(By.ID, eid))


def _find_search_input(driver, override_id=None):
    el = _by_id(driver, override_id)
    if el:
        return el
    el = _first_visible(driver.find_elements(By.XPATH,
        "//*[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
        "'search for')]/following::input[@type='text'][1]"))
    if el:
        return el
    for inp in driver.find_elements(By.XPATH, "//input[@type='text']"):
        if 'search' in (inp.get_attribute('placeholder') or '').lower() and inp.is_displayed():
            return inp
    for sid in ('PCSearchEdit_I', 'SearchEdit_I', 'POSearch_I', 'GridSearch_I', 'Search_I'):
        el = _by_id(driver, sid)
        if el:
            return el
    return None


def _find_po_in_results(driver, po_number):
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


def _find_save_button(driver, override_id=None):
    el = _by_id(driver, override_id)
    if el:
        return el
    for btn in driver.find_elements(By.XPATH, "//input[@type='button' or @type='submit']"):
        if 'save' in (btn.get_attribute('value') or '').lower() and btn.is_displayed():
            return btn
    for btn in driver.find_elements(By.XPATH, "//button"):
        if 'save' in btn.text.lower() and btn.is_displayed():
            return btn
    el = _first_visible(driver.find_elements(By.XPATH,
        "//a[contains(translate(normalize-space(text()),"
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'save')]"))
    if el:
        return el
    # EditFormButton_CD confirmed by Chrome recorder; _I kept as fallback.
    for sid in ('EditFormButton_CD', 'EditFormButton_I',
                'SaveButton_I', 'btnSave', 'SaveBtn_I', 'Save_I', 'POSave_I'):
        el = _by_id(driver, sid)
        if el:
            return el
    return None


# ── product grid scanner ──────────────────────────────────────────────────────

_FIND_GRID_JS = """
// ── Primary: DevExpress row IDs confirmed by Chrome Recorder ─────────────────
// The recording showed:
//   ORDERED  = td:nth-of-type(15)  →  index 14 (0-based)
//   SHIPPED  = td immediately after  →  index 15 (0-based)
//   Row IDs  = POProducts_DXDataRow0, DXDataRow1, ...
var ORDERED_IDX = 14;
var SHIPPED_IDX = 15;

var dxRows = [];
for (var i = 0; ; i++) {
    var row = document.getElementById('POProducts_DXDataRow' + i);
    if (!row) break;
    var cells = Array.from(row.querySelectorAll('td'));
    if (cells.length <= Math.max(ORDERED_IDX, SHIPPED_IDX)) { continue; }

    var oCell = cells[ORDERED_IDX];
    var sCell = cells[SHIPPED_IDX];

    var oInput = oCell ? oCell.querySelector('input[type=text], input:not([type])') : null;
    var ordered = oInput
        ? oInput.value.trim().replace(/[^\\d.]/g, '')
        : (oCell ? oCell.textContent.trim().replace(/[^\\d.]/g, '') : '');
    var shipped = sCell ? sCell.textContent.trim().replace(/[^\\d.]/g, '') : '';

    dxRows.push({
        row_idx:         i,
        row_id:          'POProducts_DXDataRow' + i,
        ordered_col_idx: ORDERED_IDX,
        shipped_col_idx: SHIPPED_IDX,
        ordered_val:     ordered,
        shipped_val:     shipped,
        has_input:       !!oInput,
        input_id:        oInput ? (oInput.id || '') : '',
    });
}

if (dxRows.length > 0) {
    return { found: true, rows: dxRows,
             ordered_col: ORDERED_IDX, shipped_col: SHIPPED_IDX,
             method: 'dx_rows' };
}

// ── Fallback: scan tables for ORDERED / SHIPPED headers ──────────────────────
// DevExpress splits header and body into separate tables, so we search all
// tables on the page and use the first one that contains both column names.
var tables = Array.from(document.querySelectorAll('table'));
for (var t of tables) {
    var headerCells = Array.from(t.querySelectorAll(
        'thead th, thead td, tr:first-child th, tr:first-child td'));
    var texts = headerCells.map(function(h) {
        return h.textContent.trim().toUpperCase().replace(/\\s+/g, ' ');
    });
    var oi = texts.findIndex(function(x){ return x === 'ORDERED'; });
    var si = texts.findIndex(function(x){ return x === 'SHIPPED'; });
    if (oi === -1 || si === -1) continue;

    var dataRows = Array.from(t.querySelectorAll('tbody tr, tr')).filter(function(row){
        if (row.querySelectorAll('th').length &&
            !row.querySelectorAll('td').length) return false;
        return row.querySelectorAll('td').length > Math.max(oi, si);
    });

    var rows = dataRows.map(function(row, idx) {
        var cells = Array.from(row.querySelectorAll('td'));
        var oCell = cells[oi];
        var sCell = cells[si];
        var shipped = sCell ? sCell.textContent.trim().replace(/[^\\d.]/g, '') : '';
        var oInput = oCell ? oCell.querySelector('input[type=text], input:not([type])') : null;
        var ordered = oInput
            ? oInput.value.trim().replace(/[^\\d.]/g, '')
            : (oCell ? oCell.textContent.trim().replace(/[^\\d.]/g, '') : '');
        return {
            row_idx:         idx,
            row_id:          row.id || '',
            ordered_col_idx: oi,
            shipped_col_idx: si,
            ordered_val:     ordered,
            shipped_val:     shipped,
            has_input:       !!oInput,
            input_id:        oInput ? (oInput.id || '') : '',
        };
    });

    return { found: true, rows: rows, ordered_col: oi, shipped_col: si,
             method: 'header_scan' };
}

return { found: false, rows: [], ordered_col: -1, shipped_col: -1 };
"""


def _set_cell_value(driver, cell_el, value):
    """Click into a grid cell and replace its value with *value*.

    The Chrome Recorder confirmed the DevExpress flow:
      1. Click the ORDERED td  → DevExpress reveals POProducts_DXEditor13_I
      2. Type the new value
      3. Press Enter twice  (first Enter commits the cell; second clears
         any DevExpress confirmation prompt)
    """
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cell_el)
    cell_el.click()
    time.sleep(0.5)   # DevExpress needs a moment to show the editor

    # 1. Try the confirmed DevExpress editor ID first (recorder-verified).
    inp = None
    for editor_id in ('POProducts_DXEditor13_I',):
        els = driver.find_elements(By.ID, editor_id)
        if els and els[0].is_displayed():
            inp = els[0]
            break

    # 2. Look for any input that appeared inside the cell.
    if not inp:
        inp = _first_visible(cell_el.find_elements(By.XPATH,
            ".//input[@type='text' or not(@type)]"))

    # 3. DevExpress sometimes places the editor outside the cell; grab focus.
    if not inp:
        try:
            inp = driver.switch_to.active_element
            if inp.tag_name.lower() not in ('input', 'textarea'):
                inp = None
        except Exception:
            inp = None

    if not inp:
        return False

    inp.send_keys(Keys.CONTROL + 'a')
    inp.send_keys(str(value))
    # Two Enters — confirmed by recorder; first commits, second clears any prompt.
    inp.send_keys(Keys.ENTER)
    time.sleep(0.2)
    inp.send_keys(Keys.ENTER)
    time.sleep(0.3)
    return True


# ── per-PO logic ──────────────────────────────────────────────────────────────

def _partial_single_po(driver, log, base_url, po, search_id, save_id):
    # ── Navigate to the PO search page ───────────────────────────────────
    log(f"  [INFO] Loading PO search page…")
    driver.get(base_url)
    wait_for_page(driver, 3)

    # ── Search for PO ────────────────────────────────────────────────────
    log(f"  [INFO] Locating 'search for' input…")
    search_box = _find_search_input(driver, search_id or None)
    if not search_box:
        raise RuntimeError(
            "Could not find the 'search for' input. "
            "Set search_input_id in Extra Parameters."
        )
    search_box.clear()
    search_box.send_keys(po)
    log(f"  [INFO] Typed {po} — pressing Enter…")
    search_box.send_keys(Keys.RETURN)
    time.sleep(2)

    # ── Click the PO row in results ───────────────────────────────────────
    log(f"  [INFO] Scanning results for PO {po}…")
    po_el = _find_po_in_results(driver, po)
    if not po_el:
        raise RuntimeError(f"PO {po} not found in the results grid.")
    log(f"  [INFO] Found — clicking to open detail…")
    po_el.click()
    wait_for_page(driver, 3)
    log(f"  [INFO] Detail URL: {driver.current_url}")

    # ── Scan product rows ─────────────────────────────────────────────────
    log(f"  [INFO] Scanning product rows for ORDERED / SHIPPED columns…")
    grid = driver.execute_script(_FIND_GRID_JS)

    if not grid.get("found"):
        raise RuntimeError(
            "Could not find a table with both ORDERED and SHIPPED columns. "
            "Run Inspect Page on the PO detail URL to see what's on the page."
        )

    rows        = grid["rows"]
    ordered_col = grid["ordered_col"]
    log(f"  [INFO] {len(rows)} product line(s) found")

    # ── Edit each line where ORDERED ≠ SHIPPED ────────────────────────────
    changes = 0
    for row in rows:
        shipped = row["shipped_val"]
        ordered = row["ordered_val"]
        idx     = row["row_idx"]

        if not shipped:
            log(f"  [SKIP] Line {idx+1}: SHIPPED is blank — skipping")
            continue

        if ordered == shipped:
            log(f"  [OK]   Line {idx+1}: ORDERED={ordered} already matches SHIPPED")
            continue

        log(f"  [EDIT] Line {idx+1}: ORDERED {ordered} → {shipped} (SHIPPED)")

        # Re-fetch the cell fresh (stale element guard)
        try:
            row_el   = None
            if row["row_id"]:
                els = driver.find_elements(By.ID, row["row_id"])
                if els:
                    row_el = els[0]

            if row_el:
                cells   = row_el.find_elements(By.TAG_NAME, "td")
                if len(cells) > ordered_col:
                    ok = _set_cell_value(driver, cells[ordered_col], shipped)
                else:
                    ok = False
            else:
                # Fallback: use JS to set the value directly
                ok = driver.execute_script("""
                    var tables = document.querySelectorAll('table');
                    var col = arguments[0], ri = arguments[1], val = arguments[2];
                    for (var t of tables) {
                        var drows = Array.from(t.querySelectorAll(
                            'tbody tr, tr')).filter(function(r){
                                return !r.querySelectorAll('th').length
                                    && r.querySelectorAll('td').length > col;
                        });
                        if (ri < drows.length) {
                            var cell = drows[ri].querySelectorAll('td')[col];
                            var inp  = cell.querySelector('input');
                            if (inp) {
                                inp.value = val;
                                inp.dispatchEvent(new Event('input',  {bubbles:true}));
                                inp.dispatchEvent(new Event('change', {bubbles:true}));
                                return true;
                            }
                            cell.click();
                            return 'clicked';
                        }
                    }
                    return false;
                """, ordered_col, idx, shipped)

                if ok == 'clicked':
                    time.sleep(0.4)
                    try:
                        inp = driver.switch_to.active_element
                        if inp.tag_name.lower() in ('input', 'textarea'):
                            inp.send_keys(Keys.CONTROL + 'a')
                            inp.send_keys(str(shipped))
                            inp.send_keys(Keys.TAB)
                            ok = True
                    except Exception:
                        ok = False

            if ok:
                changes += 1
            else:
                log(f"  [WARN] Line {idx+1}: could not edit ORDERED cell")

        except StaleElementReferenceException:
            log(f"  [WARN] Line {idx+1}: page changed mid-edit — re-scan needed")

    if changes == 0:
        log(f"  [INFO] No changes made — all ORDERED values already match SHIPPED.")
        return

    # ── Save ──────────────────────────────────────────────────────────────
    log(f"  [INFO] {changes} line(s) edited — locating Save button…")
    save_btn = _find_save_button(driver, save_id or None)
    if not save_btn:
        raise RuntimeError(
            "Could not find the Save button. "
            "Set save_button_id in Extra Parameters."
        )
    save_btn.click()
    log(f"  [INFO] Saved — waiting for page response…")
    time.sleep(2)
    log(f"  [INFO] Post-save URL: {driver.current_url}")


# ── main entry point ──────────────────────────────────────────────────────────

def run(log, excel_path, cookies, params):
    url = params.get("url", "").strip()
    if not url:
        log("[ERROR] No URL supplied.")
        log("[ERROR] Paste the Select Sales PO search URL into the URL box.")
        return

    raw = params.get("po_numbers", [])
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split("\n") if p.strip()]
    po_numbers = [str(p).strip().zfill(10) for p in raw if str(p).strip()]

    if not po_numbers:
        log("[ERROR] No PO numbers provided. Enter them in the PO Numbers box.")
        return

    search_id = params.get("search_input_id", "")
    save_id   = params.get("save_button_id",   "")

    log(f"[INFO] POs to process ({len(po_numbers)}):")
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
                _partial_single_po(driver, log, url, po, search_id, save_id)
                success_count += 1
                log(f"  [SUCCESS] PO {po} partial adjustment saved.")
            except Exception as exc:
                fail_count += 1
                log(f"  [ERROR] PO {po} — {exc}")
            log("─" * 60)
    finally:
        driver.quit()
        log("[INFO] Browser closed.")

    log(f"[RESULT] Finished — {success_count} saved, {fail_count} failed.")
