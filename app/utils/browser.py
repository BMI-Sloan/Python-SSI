"""
Browser utility - sets up a Chrome WebDriver instance with optional cookies.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional


def _must_be_headless() -> bool:
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


def make_driver(
    cookies: Optional[list[dict]] = None,
    headless: bool = True,
    initial_url: Optional[str] = None,
    download_dir: Optional[str] = None,
) -> webdriver.Chrome:
    opts = Options()

    if headless or _must_be_headless():
        opts.add_argument("--headless=new")

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    if download_dir:
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        opts.add_experimental_option("prefs", prefs)

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    except Exception:
        service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    driver.implicitly_wait(10)

    if cookies:
        # Inject cookies via CDP *before* any navigation.
        # driver.add_cookie() requires the browser to already be on the
        # target domain — but the site redirects to a login page (data:,)
        # before any cookies are set, so add_cookie lands on the wrong
        # domain and the session is never established.
        # Network.setCookie has no domain restriction and takes effect
        # for the very first request.
        driver.execute_cdp_cmd("Network.enable", {})
        for cookie in cookies:
            cmd: dict = {
                "name":   str(cookie.get("name",  "")),
                "value":  str(cookie.get("value", "")),
                "domain": str(cookie.get("domain", "")),
                "path":   str(cookie.get("path",  "/")),
            }
            if cookie.get("secure"):
                cmd["secure"] = True
            if cookie.get("httpOnly"):
                cmd["httpOnly"] = True
            if cookie.get("expiry"):
                cmd["expires"] = int(cookie["expiry"])
            try:
                driver.execute_cdp_cmd("Network.setCookie", cmd)
            except Exception as exc:
                print(f"[WARN] Could not set cookie '{cookie.get('name')}': {exc}")

    if initial_url:
        driver.get(initial_url)
        time.sleep(2)

    return driver


def wait_for_page(driver: webdriver.Chrome, seconds: float = 2.0) -> None:
    time.sleep(seconds)
    driver.execute_script("return document.readyState") == "complete"
