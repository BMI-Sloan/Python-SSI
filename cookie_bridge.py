"""
cookie_bridge.py — local helper that proxies Chrome CDP cookie requests.

Run this on your Windows machine alongside launch_chrome.bat.
It listens on localhost:9223 and forwards cookie requests to Chrome's
CDP endpoint on localhost:9222, so the Railway app can reach them via
a reverse tunnel or direct connection.

Usage:
    python cookie_bridge.py
    python cookie_bridge.py --cdp-port 9222 --bridge-port 9223
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cookie_bridge")


# ─────────────────────────────────────────────────────────────────────────────
#  CDP helpers (sync wrappers around websockets)
# ─────────────────────────────────────────────────────────────────────────────

def _get_pages(cdp_port: int) -> list[dict]:
    url = f"http://127.0.0.1:{cdp_port}/json/list"
    with urllib.request.urlopen(url, timeout=4) as r:
        return json.loads(r.read())


async def _fetch_cookies_async(ws_url: str) -> list[dict]:
    import websockets  # type: ignore

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        for _ in range(30):
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            if msg.get("id") == 1:
                return msg.get("result", {}).get("cookies", [])
    return []


def fetch_cookies(cdp_port: int) -> dict:
    try:
        pages = _get_pages(cdp_port)
    except Exception as exc:
        return {"success": False, "cookies": [], "message": f"Cannot reach Chrome on port {cdp_port}: {exc}"}

    ws_url = None
    for page in pages:
        if page.get("type") == "page" and "select-sales" in page.get("url", "").lower():
            ws_url = page.get("webSocketDebuggerUrl")
            break
    if not ws_url:
        for page in pages:
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                ws_url = page.get("webSocketDebuggerUrl")
                break

    if not ws_url:
        return {"success": False, "cookies": [], "message": "No Chrome tab found."}

    try:
        all_cookies = asyncio.run(_fetch_cookies_async(ws_url))
    except Exception as exc:
        return {"success": False, "cookies": [], "message": f"CDP error: {exc}"}

    ss_cookies = [
        {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        for c in all_cookies
        if "select-sales" in c.get("domain", "").lower()
    ]

    if ss_cookies:
        return {
            "success": True,
            "cookies": ss_cookies,
            "message": f"Captured {len(ss_cookies)} Select Sales cookie(s).",
        }

    # Fall back: return all cookies so user can filter
    all_simple = [
        {"name": c["name"], "value": c["value"],
         "domain": c.get("domain", ""), "path": c.get("path", "/")}
        for c in all_cookies
    ]
    return {
        "success": bool(all_simple),
        "cookies": all_simple,
        "message": (
            f"No Select-Sales cookies found. Returning all {len(all_simple)} cookie(s) — "
            "make sure you are logged in to Select Sales."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP server
# ─────────────────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    cdp_port: int = 9222

    def log_message(self, fmt, *args):
        log.info("HTTP %s", fmt % args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/cookies", "/cookies/"):
            result = fetch_cookies(self.cdp_port)
            self._send_json(result)
        elif self.path in ("/health", "/"):
            self._send_json({"status": "ok", "cdp_port": self.cdp_port})
        else:
            self._send_json({"error": "Not found"}, 404)


def make_handler(cdp_port: int):
    class Handler(_Handler):
        pass
    Handler.cdp_port = cdp_port
    return Handler


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cookie bridge for SSI Interact")
    parser.add_argument("--cdp-port",    type=int, default=9222, help="Chrome CDP port (default 9222)")
    parser.add_argument("--bridge-port", type=int, default=9223, help="Bridge listen port (default 9223)")
    parser.add_argument("--host",        default="127.0.0.1",    help="Bind host (default 127.0.0.1)")
    args = parser.parse_args()

    try:
        import websockets  # noqa: F401
    except ImportError:
        log.error("'websockets' package not found. Run: pip install websockets")
        sys.exit(1)

    server = HTTPServer((args.host, args.bridge_port), make_handler(args.cdp_port))
    log.info("Cookie bridge listening on http://%s:%d", args.host, args.bridge_port)
    log.info("Forwarding CDP requests to Chrome on port %d", args.cdp_port)
    log.info("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
