"""
Partial PO — for each PO number, opens the PO detail and sets every
product line's ORDERED qty to match its SHIPPED qty, then saves.

Enter PO numbers in the PO Numbers box in the sidebar.
No URL or Extra Parameters needed — the site address is built in.

Steps (mirrors the Chrome Recorder recording):
  1. Navigate to http://edw.select-sales.com/PO
  2. Type the PO number into #POKeywordsFilter_I and press Enter
  3. Click the first result row
  4. For every product row where ORDERED ≠ SHIPPED:
       a. Click the ORDERED cell (td index 14, confirmed by recording)
       b. Wait for #POProducts_DXEditor13_I to appear
       c. Type the SHIPPED value, press Enter twice
  5. Click Save (#EditFormButton_CD)
  6. Reset browser to PO list and repeat for the next PO number
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

# ── site constants — confirmed by Chrome Recorder ────────────────────────────
_HOME        = 'http://edw.select-sales.com/'
_PO_LIST     = 'http://edw.select-sales.com/PO'
_SEARCH      = 'POKeywordsFilter_I'
_ROW0_CELL   = '#POResults_DXDataRow0 > td:nth-of-type(3)'
_ORDERED_IDX = 14   # td:nth-of-type(15) — confirmed by recording
_SHIPPED_IDX = 16   # td[15] = OPEN; SHIPPED is one further at td[16]
_DX_EDITOR   = 'POProducts_DXEditor13_I'
_SAVE        = 'EditFormButton_CD'
_WAIT        = 15


def _go_to_po_list(driver):
    """Navigate directly to the PO list page and wait for the search input."""
    driver.get(_PO_LIST)
    try:
        WebDriverWait(driver, _WAIT).until(
            EC.presence_of_element_located((By.ID, _SEARCH))
        )
    except TimeoutException:
        pass   # best effort — main loop will catch any real problems


def _click_cell(driver, row_id, cell_idx, max_attempts=3):
    """
    Re-fetch the row and click the cell at cell_idx.
    Retries up to max_attempts times to handle DevExpress grid re-renders
    that cause StaleElementReferenceException.
    Returns True on success, False if all attempts fail.
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


def _read_row_values(driver, row_id):
    """
    Read ORDERED and SHIPPED text from a row without holding onto element refs.
    Returns (ordered_text, shipped_text) or (None, None) if row not found.
    """
    try:
        row_el = driver.find_element(By.ID, row_id)
        cells  = row_el.find_elements(By.TAG_NAME, 'td')
        if len(cells) <= _SHIPPED_IDX:
            return None, None
        ordered = cells[_ORDERED_IDX].text.strip()
        shipped = cells[_SHIPPED_IDX].text.strip()
        return ordered, shipped
    except (NoSuchElementException, StaleElementReferenceException):
        return None, None


def _partial_single_po(driver, log, po):
    wait = WebDriverWait(driver, _WAIT)

    # ── Navigate to PO list ───────────────────────────────────────────────
    _go_to_po_list(driver)

    # ── Type PO number and press Enter ────────────────────────────────────
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

    # ── Click first result row ────────────────────────────────────────────
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

    # ── Wait for product table WITH actual data ───────────────────────────
    # After the URL changes, DevExpress may still be rendering the new page's
    # grid. We wait until the ORDERED column cell (index 14) in the first row
    # has real text — that confirms the grid finished loading the new PO's data
    # and we are not reading stale DOM from the previous page.
    try:
        wait.until(EC.presence_of_element_located((By.ID, 'POProducts_DXDataRow0')))
        wait.until(lambda d: bool(d.execute_script("""
            var row = document.getElementById('POProducts_DXDataRow0');
            if (!row) return false;
            var cells = row.querySelectorAll('td');
            return cells.length > 14 && cells[14].textContent.trim() !== '';
        """)))
    except TimeoutException:
        log(f"  [INFO] No product rows found — nothing to adjust.")
        return

    # ── Diagnostic: dump td[10..19] of first row to confirm column layout ─
    try:
        diag = driver.execute_script("""
            var row = document.getElementById('POProducts_DXDataRow0');
            if (!row) return null;
            var cells = row.querySelectorAll('td');
            var out = [];
            for (var i = 10; i < Math.min(20, cells.length); i++) {
                out.push(i + ':' + cells[i].textContent.trim());
            }
            return out.join(' | ');
        """)
        if diag:
            log(f"  [DIAG] Row 0 td[10-19]: {diag}")
    except Exception:
        pass

    # ── Pass 1: read all row values up front (no element refs kept) ───────
    # Reading text and clicking in the same loop causes stale refs because
    # DevExpress re-renders after each click. We scan first, then edit.
    rows_to_edit = []   # list of (row_n, row_id, shipped_text)
    row_n = 0

    while True:
        row_id = f'POProducts_DXDataRow{row_n}'
        ordered, shipped = _read_row_values(driver, row_id)

        if ordered is None and shipped is None:
            # Check if row truly doesn't exist vs a read error
            if not driver.find_elements(By.ID, row_id):
                break   # no more rows
            row_n += 1
            continue

        if not shipped:
            log(f"  [SKIP] Row {row_n+1}: SHIPPED is blank")
        elif ordered == shipped:
            log(f"  [OK]   Row {row_n+1}: ORDERED={ordered} already matches SHIPPED")
        else:
            log(f"  [EDIT] Row {row_n+1}: ORDERED {ordered} → {shipped}")
            rows_to_edit.append((row_n, row_id, shipped))

        row_n += 1

    if not rows_to_edit:
        log(f"  [INFO] All ORDERED quantities already match SHIPPED — nothing to save.")
        return

    # ── Pass 2: click and edit each mismatched cell ───────────────────────
    changes = 0
    for row_n, row_id, shipped_text in rows_to_edit:

        # Re-fetch cells fresh and click with retry (handles DevExpress re-renders)
        clicked = _click_cell(driver, row_id, _ORDERED_IDX)
        if not clicked:
            log(f"  [WARN] Row {row_n+1}: could not click ORDERED cell — skipping")
            continue

        time.sleep(0.5)   # DevExpress needs a moment to show the editor

        # Wait for the DevExpress editor (#POProducts_DXEditor13_I)
        try:
            editor = wait.until(EC.presence_of_element_located((By.ID, _DX_EDITOR)))
        except TimeoutException:
            log(f"  [WARN] Row {row_n+1}: editor #{_DX_EDITOR} did not appear — skipping")
            continue

        # Type value, Enter × 2 (matches recording exactly)
        editor.send_keys(Keys.CONTROL + 'a')
        editor.send_keys(shipped_text)
        editor.send_keys(Keys.ENTER)
        time.sleep(0.2)
        try:
            editor.send_keys(Keys.ENTER)   # second Enter; editor may already be gone
        except StaleElementReferenceException:
            pass   # that's fine — first Enter committed it
        time.sleep(0.3)

        changes += 1

    if changes == 0:
        log(f"  [INFO] No edits succeeded — skipping Save.")
        return

    # ── Click Save ────────────────────────────────────────────────────────
    # The recording used pierce/#EditFormButton_CD, meaning the Save button
    # may be inside a Shadow DOM. Selenium's .click() fails with "element not
    # interactable" for Shadow DOM elements, so we use JavaScript instead.
    log(f"  [INFO] {changes} row(s) updated — clicking Save…")
    save_result = driver.execute_script("""
        // Strategy 1 — direct getElementById + JS click
        var el = document.getElementById('EditFormButton_CD');
        if (el) {
            el.scrollIntoView({block: 'center'});
            el.click();
            return 'id';
        }

        // Strategy 2 — deep shadow root search
        function deepFind(root) {
            var found = root.querySelector ? root.querySelector('#EditFormButton_CD') : null;
            if (found) return found;
            var nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
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

        // Strategy 3 — find by visible "Save" text
        var candidates = Array.from(document.querySelectorAll(
            'a, button, input[type=button], input[type=submit]'
        ));
        for (var c of candidates) {
            var text = (c.textContent || c.value || '').trim().toLowerCase();
            if (text === 'save') {
                c.scrollIntoView({block: 'center'});
                c.click();
                return 'text';
            }
        }
        return 'not_found';
    """)
    if save_result == 'not_found':
        raise RuntimeError(
            f"Could not find Save button (#{_SAVE}) via element ID, "
            "shadow DOM search, or text. The form may still be in edit mode."
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

    log(f"[RESULT] Finished — {success_count} done, {fail_count} failed.")
