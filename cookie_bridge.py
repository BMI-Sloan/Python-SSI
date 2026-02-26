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
import importlib.util
import json
import logging
import queue
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cookie_bridge")

# ─────────────────────────────────────────────────────────────────────────────
#  Local script runner
#  Scripts live in app/scripts/ (same repo).  The bridge adds app/ to
#  sys.path so scripts can do `from utils.browser import make_driver`.
# ─────────────────────────────────────────────────────────────────────────────

_APP_DIR = Path(__file__).parent / "app"


def _ensure_app_path() -> None:
    app_str = str(_APP_DIR)
    if app_str not in sys.path:
        sys.path.insert(0, app_str)


_jobs: dict = {}
_jobs_lock = threading.Lock()


def _start_local_run(script_name: str, cookies: list, params: dict,
                     sources: "dict | None" = None) -> str:
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "logs": [], "done": False}
    t = threading.Thread(
        target=_run_thread,
        args=(job_id, script_name, sources, cookies, params),
        daemon=True,
    )
    t.start()
    return job_id


def _run_thread(job_id: str, script_name: str, sources: "dict | None",
                cookies: list, params: dict) -> None:
    """
    Execute a script either from a Railway-bundled sources dict (preferred)
    or from the local app/scripts/ directory (legacy fallback).

    When sources is provided the script + utils are written to a temporary
    directory so the full repository does NOT need to be present locally.
    """
    import shutil
    import tempfile

    job     = _jobs[job_id]
    tmp_dir = None

    def _log(msg: str) -> None:
        with _jobs_lock:
            job["logs"].append(str(msg))

    try:
        if sources:
            # ── Bundled path: write sources to a temp dir ──────────────────
            tmp_dir = Path(tempfile.mkdtemp(prefix="ssi_bridge_"))

            for rel_path, content in sources.items():
                dest = tmp_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

            # Guarantee utils is a proper package
            utils_init = tmp_dir / "utils" / "__init__.py"
            if not utils_init.exists():
                utils_init.write_text("", encoding="utf-8")

            script_file = tmp_dir / "scripts" / f"{script_name}.py"
            sys.path.insert(0, str(tmp_dir))
        else:
            # ── Legacy path: full repo must be present beside bridge ────────
            _ensure_app_path()
            script_file = _APP_DIR / "scripts" / f"{script_name}.py"

        if not script_file.exists():
            raise FileNotFoundError(
                f"Script '{script_name}' not found at {script_file}."
            )

        # Use a unique module name so repeated runs don't hit the module cache.
        mod_name = f"ssi_{script_name}_{job_id[:8]}"
        spec   = importlib.util.spec_from_file_location(mod_name, script_file)
        module = importlib.util.module_from_spec(spec)   # type: ignore[arg-type]
        spec.loader.exec_module(module)                  # type: ignore[union-attr]

        _log(f"[INFO] Starting script: {script_name}")
        module.run(log=_log, excel_path=None, cookies=cookies, params=params)
        _log("[INFO] Script completed successfully.")
        job["status"] = "completed"

    except Exception as exc:
        _log(f"[ERROR] {exc}")
        job["status"] = "failed"

    finally:
        with _jobs_lock:
            job["done"] = True

        if tmp_dir is not None:
            # Remove temp dir from sys.path
            tmp_str = str(tmp_dir)
            if tmp_str in sys.path:
                sys.path.remove(tmp_str)
            # Evict cached utils modules so next run gets a fresh import
            stale = [k for k in list(sys.modules) if k.startswith("utils") or k.startswith("ssi_")]
            for k in stale:
                sys.modules.pop(k, None)
            # Delete the temp directory
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Fast page-element inspector (no Selenium — plain HTTP + HTML parser)
#  Works with any URL reachable from this machine (VPN routes included).
# ─────────────────────────────────────────────────────────────────────────────

from html.parser import HTMLParser as _HTMLParser


class _FormElementParser(_HTMLParser):
    """Extracts interactive element metadata from raw HTML using stdlib only."""
    _TRACKED = frozenset(("input", "select", "textarea", "button"))

    def __init__(self):
        super().__init__()
        self.elements: list = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in self._TRACKED:
            return
        ad = dict(attrs)
        el_type = ad.get("type", "").lower()
        if el_type == "hidden":
            return
        self.elements.append({
            "tag":         tag,
            "id":          ad.get("id",          ""),
            "name":        ad.get("name",        ""),
            "type":        el_type,
            "placeholder": ad.get("placeholder", ""),
            "value":       ad.get("value",       "") if el_type != "password" else "",
        })

    def error(self, message: str) -> None:  # suppress malformed-HTML errors
        pass


def _fetch_page_elements(url: str, cookies: list) -> dict:
    """GET url with the given session cookies, parse HTML, return element list."""
    cookie_str = "; ".join(
        f"{c['name']}={c['value']}"
        for c in cookies
        if c.get("name") and c.get("value")
    )
    req = urllib.request.Request(url)
    req.add_header("User-Agent",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    req.add_header("Accept", "text/html,application/xhtml+xml,*/*;q=0.9")
    if cookie_str:
        req.add_header("Cookie", cookie_str)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html      = resp.read().decode("utf-8", errors="replace")
            final_url = resp.url
    except Exception as exc:
        return {"success": False, "error": str(exc), "elements": [], "final_url": url}

    parser = _FormElementParser()
    parser.feed(html)
    elements = [e for e in parser.elements if e["id"] or e["name"]]
    return {
        "success":     True,
        "final_url":   final_url,
        "html_length": len(html),
        "elements":    elements,
    }


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
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/cookies", "/cookies/"):
            result = fetch_cookies(self.cdp_port)
            self._send_json(result)
        elif self.path in ("/health", "/"):
            self._send_json({"status": "ok", "cdp_port": self.cdp_port})
        elif self.path.startswith("/stream/"):
            job_id = self.path.split("/stream/", 1)[1].split("?")[0]
            self._stream_job(job_id)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if self.path in ("/run", "/run/"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                self._send_json({"error": f"Invalid JSON: {exc}"}, 400)
                return
            script  = str(data.get("script",  "")).strip()
            cookies = data.get("cookies", [])
            params  = data.get("params",  {})
            sources = data.get("sources", None)   # bundled from Railway
            if not script:
                self._send_json({"error": "script name is required"}, 400)
                return
            job_id = _start_local_run(
                script,
                cookies if isinstance(cookies, list) else [],
                params,
                sources if isinstance(sources, dict) else None,
            )
            self._send_json({"job_id": job_id})
        elif self.path in ("/inspect-url", "/inspect-url/"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                self._send_json({"error": f"Invalid JSON: {exc}"}, 400)
                return
            url     = str(data.get("url",     "")).strip()
            cookies = data.get("cookies", [])
            if not url:
                self._send_json({"error": "url is required"}, 400)
                return
            result = _fetch_page_elements(url, cookies if isinstance(cookies, list) else [])
            self._send_json(result)
        else:
            self._send_json({"error": "Not found"}, 404)

    def _stream_job(self, job_id: str) -> None:
        if job_id not in _jobs:
            self._send_json({"error": "job not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type",  "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        sent = 0
        try:
            while True:
                with _jobs_lock:
                    snapshot = list(_jobs[job_id]["logs"])
                    done     = _jobs[job_id]["done"]
                batch = snapshot[sent:]
                for line in batch:
                    self.wfile.write(f"data: {line}\n\n".encode())
                sent += len(batch)
                if batch:
                    self.wfile.flush()
                if done and sent >= len(snapshot):
                    status = _jobs[job_id]["status"]
                    self.wfile.write(f"event: done\ndata: {status}\n\n".encode())
                    self.wfile.flush()
                    break
                time.sleep(0.15)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected


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
