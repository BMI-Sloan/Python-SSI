"""
Inspect Page — diagnostic script.
Navigate to the target URL with session cookies and dump all interactive elements.

Usage:
    Extra Parameters: {"url": "https://your-selectsales-url.com"}
"""

from utils.browser import make_driver, wait_for_page
from selenium.webdriver.common.by import By


def run(log, excel_path, cookies, params):
    url = params.get("url", "").strip()
    if not url:
        log('[ERROR] No URL supplied.')
        log('[ERROR] Paste the Select Sales URL into Extra Parameters, e.g.:')
        log('[ERROR]   {"url": "https://app.select-sales.com"}')
        return

    log(f"[INFO] Opening → {url}")
    try:
        driver = make_driver(cookies=cookies, headless=True, initial_url=url)
    except Exception as exc:
        if "ERR_NAME_NOT_RESOLVED" in str(exc) or "net::" in str(exc):
            log(f"[ERROR] Cannot reach {url!r} — check the URL in Extra Parameters.")
        else:
            log(f"[ERROR] Browser failed to start: {exc}")
        return

    try:
        wait_for_page(driver, 3)
        log(f"[INFO] Page title: {driver.title}")
        log(f"[INFO] Current URL: {driver.current_url}")
        log("─" * 60)

        selects = driver.find_elements(By.TAG_NAME, "select")
        log(f"[INFO] <select> elements found: {len(selects)}")
        for i, el in enumerate(selects):
            eid = el.get_attribute("id") or ""
            ename = el.get_attribute("name") or ""
            opts = [o.text.strip() for o in el.find_elements(By.TAG_NAME, "option") if o.text.strip()]
            log(f"  [{i+1}] id={eid!r}  name={ename!r}")
            log(f"       options: {opts[:8]}{'…' if len(opts) > 8 else ''}")

        log("─" * 60)

        js_dropdowns = driver.execute_script("""
            var hits = [];
            var all = document.querySelectorAll('*');
            var keywords = ['dropdown', 'select', 'picker', 'menu', 'nav', 'filter', 'combo'];
            all.forEach(function(el) {
                var cls = (el.className || '').toLowerCase();
                var id  = (el.id || '').toLowerCase();
                var tag = el.tagName.toLowerCase();
                if (['script','style','svg','path','meta','link'].includes(tag)) return;
                for (var k of keywords) {
                    if (cls.includes(k) || id.includes(k)) {
                        var txt = (el.innerText || '').trim().substring(0, 80);
                        hits.push({tag: tag, id: el.id, cls: el.className, txt: txt});
                        break;
                    }
                }
            });
            return hits.slice(0, 40);
        """)
        log(f"[INFO] Keyword-matched elements: {len(js_dropdowns)}")
        for i, el in enumerate(js_dropdowns):
            log(f"  [{i+1}] <{el['tag']}> id={el['id']!r}")
            if el['txt']:
                log(f"       text: {el['txt']!r}")

        log("─" * 60)

        nav_btns = driver.execute_script("""
            var containers = document.querySelectorAll('nav, header, [role="navigation"], [role="menubar"]');
            var hits = [];
            containers.forEach(function(c) {
                c.querySelectorAll('a, button, [role="menuitem"], [role="button"]').forEach(function(el) {
                    var txt = (el.innerText || el.textContent || '').trim().substring(0, 60);
                    if (txt) hits.push({tag: el.tagName.toLowerCase(), id: el.id, txt: txt});
                });
            });
            return hits.slice(0, 40);
        """)
        log(f"[INFO] Nav/header elements: {len(nav_btns)}")
        for i, el in enumerate(nav_btns):
            log(f"  [{i+1}] <{el['tag']}> id={el['id']!r}  text={el['txt']!r}")

        log("─" * 60)

        buttons = driver.execute_script("""
            var hits = [];
            document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]').forEach(function(el) {
                var txt = (el.innerText || el.value || el.textContent || '').trim().substring(0, 60);
                if (txt) hits.push({tag: el.tagName.toLowerCase(), id: el.id, txt: txt});
            });
            return hits.slice(0, 40);
        """)
        log(f"[INFO] Buttons: {len(buttons)}")
        for i, el in enumerate(buttons):
            log(f"  [{i+1}] <{el['tag']}> id={el['id']!r}  text={el['txt']!r}")

        log("─" * 60)
        log("[INFO] Done. Review the output above to identify the elements you need.")

    finally:
        driver.quit()
        log("[INFO] Browser closed.")
