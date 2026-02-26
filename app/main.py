"""
SSI Interact - Web Script Runner
FastAPI backend: handles file uploads, script execution, and live log streaming.
"""

import asyncio
import importlib
import json
import os
import queue
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="SSI Interact", version="1.0.0")

STATIC_DIR = Path(__file__).parent / "static"

_default_uploads = Path(__file__).parent.parent / "uploads"
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(_default_uploads)))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def _run_script_thread(job_id: str, script_name: str, excel_path: Optional[str],
                        cookies_json: str, extra_params: dict) -> None:
    log_q: queue.Queue = jobs[job_id]["log_queue"]

    def log(msg: str) -> None:
        line = str(msg)
        log_q.put(line)
        with jobs_lock:
            jobs[job_id]["logs"].append(line)

    try:
        with jobs_lock:
            jobs[job_id]["status"] = "running"

        log(f"[INFO] Starting script: {script_name}")

        try:
            module = importlib.import_module(f"scripts.{script_name}")
        except ModuleNotFoundError:
            raise RuntimeError(f"Script '{script_name}' not found in app/scripts/")

        cookies = []
        if cookies_json.strip():
            try:
                raw = json.loads(cookies_json)
                if isinstance(raw, list):
                    cookies = raw
                elif isinstance(raw, dict):
                    cookies = [raw]
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid cookies JSON: {exc}") from exc

        module.run(
            log=log,
            excel_path=excel_path,
            cookies=cookies,
            params=extra_params,
        )

        log("[INFO] Script completed successfully.")
        with jobs_lock:
            jobs[job_id]["status"] = "completed"

    except Exception as exc:
        err = f"[ERROR] {exc}"
        log_q.put(err)
        with jobs_lock:
            jobs[job_id]["logs"].append(err)
            jobs[job_id]["status"] = "failed"
    finally:
        log_q.put(None)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "1.0.0"})


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/api/scripts")
async def list_scripts() -> JSONResponse:
    scripts_dir = Path(__file__).parent / "scripts"
    scripts = [
        f.stem
        for f in scripts_dir.glob("*.py")
        if not f.name.startswith("_")
    ]
    return JSONResponse({"scripts": sorted(scripts)})


@app.get("/api/bundle/{script_name}")
async def get_script_bundle(script_name: str) -> JSONResponse:
    """Return the script + all utils as a JSON bundle for the local bridge to execute."""
    scripts_dir = Path(__file__).parent / "scripts"
    utils_dir   = Path(__file__).parent / "utils"

    script_path = scripts_dir / f"{script_name}.py"
    if not script_path.exists() or script_name.startswith("_"):
        raise HTTPException(status_code=404, detail=f"Script '{script_name}' not found")

    sources: dict[str, str] = {
        f"scripts/{script_name}.py": script_path.read_text(encoding="utf-8"),
    }
    for util_file in utils_dir.glob("*.py"):
        sources[f"utils/{util_file.name}"] = util_file.read_text(encoding="utf-8")

    return JSONResponse({"script": script_name, "sources": sources})


@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)) -> JSONResponse:
    allowed = {".xlsx", ".xls", ".csv"}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"File type '{suffix}' not allowed. Use .xlsx, .xls, or .csv")

    file_id = str(uuid.uuid4())
    dest = UPLOADS_DIR / f"{file_id}{suffix}"
    dest.write_bytes(await file.read())

    return JSONResponse({"file_id": file_id, "filename": file.filename, "path": str(dest)})


@app.post("/api/inspect-excel")
async def inspect_excel(file: UploadFile = File(...)) -> JSONResponse:
    import io
    import pandas as pd

    allowed = {".xlsx", ".xls", ".csv"}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"File type '{suffix}' not allowed. Use .xlsx, .xls, or .csv")

    content = await file.read()
    try:
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(content))
            sheets = [{
                "name": "Sheet1",
                "row_count": len(df),
                "columns": [str(c) for c in df.columns],
                "sample": df.head(5).fillna("").astype(str).to_dict(orient="records"),
            }]
        elif suffix == ".xls":
            import zipfile as _zf
            import openpyxl.reader.excel as _opxl

            magic = content[:4]
            if magic == b'\xd0\xcf\x11\xe0':
                xl = pd.ExcelFile(io.BytesIO(content), engine="xlrd")
            else:
                class _NoCrcZipFile(_zf.ZipFile):
                    def open(self, name, mode="r", pwd=None, *, force_zip64=False):
                        f = super().open(name, mode, pwd, force_zip64=force_zip64)
                        f._expected_crc = None
                        return f

                _orig = _opxl.ZipFile
                _opxl.ZipFile = _NoCrcZipFile
                try:
                    xl = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
                finally:
                    _opxl.ZipFile = _orig

            sheets = []
            for name in xl.sheet_names:
                df = xl.parse(name)
                sheets.append({
                    "name": str(name),
                    "row_count": len(df),
                    "columns": [str(c) for c in df.columns],
                    "sample": df.head(5).fillna("").astype(str).to_dict(orient="records"),
                })
        else:
            xl = pd.ExcelFile(io.BytesIO(content), engine="openpyxl")
            sheets = []
            for name in xl.sheet_names:
                df = xl.parse(name)
                sheets.append({
                    "name": str(name),
                    "row_count": len(df),
                    "columns": [str(c) for c in df.columns],
                    "sample": df.head(5).fillna("").astype(str).to_dict(orient="records"),
                })
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc

    return JSONResponse({"filename": file.filename, "sheets": sheets})


@app.get("/api/capture-cookies")
async def capture_cookies() -> JSONResponse:
    import websockets

    cdp_port = int(os.environ.get("CDP_PORT", "9222"))

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=3) as r:
            pages = json.loads(r.read())
    except Exception as exc:
        return JSONResponse({
            "success": False,
            "cookies": [],
            "message": (
                f"Cannot reach Chrome on port {cdp_port}: {exc}. "
                "Chrome must be started with --remote-debugging-port=9222."
            ),
        })

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
        return JSONResponse({
            "success": False,
            "cookies": [],
            "message": "No Chrome pages found. Is Chrome running with --remote-debugging-port=9222?",
        })

    _INSPECT_JS = """
(function() {
  try {
    var r = {title: document.title, url: window.location.href,
              knownElements: {}, visibleRowCount: 0};
    var KNOWN = {
      filters: {
        'ManufacturerFilter_I':  'Manufacturer filter',
        'TskRetailerIDFilter_I': 'Retailer filter',
        'TskTaskTypeIDFilter_I': 'Task Type filter',
        'TskKeywordsFilter_I':   'Keywords filter'
      },
      buttons: {
        'ApproveButton_I':     'Approve',
        'RejectButton_I':      'Reject',
        'ExcelExport_I':       'Export to Excel',
        'ClearTasksResults_I': 'Clear Filters'
      },
      grid: {
        'TasksResults':              'Results grid',
        'TasksResults_DXSelAllBtn0': 'Select-All checkbox',
        'TasksResults_DXSelBtn0':    'Row 1 checkbox'
      }
    };
    for (var grp in KNOWN) {
      r.knownElements[grp] = {};
      for (var eid in KNOWN[grp]) {
        var el = document.getElementById(eid);
        r.knownElements[grp][eid] = {
          label:   KNOWN[grp][eid],
          found:   !!el,
          visible: el ? (el.offsetParent !== null && el.offsetWidth > 0) : false
        };
      }
    }
    var rowBtns = document.querySelectorAll("[id^='TasksResults_DXSelBtn']");
    r.visibleRowCount = Array.from(rowBtns).filter(function(e){ return e.offsetParent !== null; }).length;
    return JSON.stringify(r);
  } catch(e) {
    return JSON.stringify({_error: String(e), title: String(document.title||''),
                           url: String(location.href||''), knownElements: {}, visibleRowCount: 0});
  }
})()
"""

    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            cookies_msg = None
            for _ in range(30):
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    cookies_msg = msg
                    break
            else:
                raise RuntimeError("CDP did not respond to Network.getAllCookies")
    except Exception as exc:
        return JSONResponse({
            "success": False,
            "cookies": [],
            "message": f"CDP cookies error: {exc}",
        })

    inspect_result = None
    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws2:
            await ws2.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
            await ws2.send(json.dumps({
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {"expression": _INSPECT_JS},
            }))
            _end = asyncio.get_running_loop().time() + 10
            while asyncio.get_running_loop().time() < _end:
                _left = _end - asyncio.get_running_loop().time()
                try:
                    raw = await asyncio.wait_for(ws2.recv(), timeout=max(0.1, _left))
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("id") == 2:
                    cdp_val = msg.get("result", {}).get("result", {}).get("value")
                    if cdp_val:
                        try:
                            inspect_result = json.loads(cdp_val)
                        except Exception:
                            inspect_result = {"_error": f"parse error: {cdp_val[:200]}"}
                    else:
                        inspect_result = {"_error": f"CDP no value: {str(msg.get('result',''))[:300]}"}
                    break
            if inspect_result is None:
                inspect_result = {"_error": "Runtime.evaluate: no response in 10s"}
    except Exception as exc:
        inspect_result = {"_error": f"inspect error: {exc}"}

    all_cookies = cookies_msg.get("result", {}).get("cookies", [])

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
        return JSONResponse({
            "success": True,
            "cookies": ss_cookies,
            "inspect": inspect_result,
            "message": f"Captured {len(ss_cookies)} Select Sales cookie(s).",
        })

    return JSONResponse({
        "success": False,
        "cookies": [],
        "inspect": inspect_result,
        "message": "No Select Sales cookies found. Make sure you are logged in to the Select Sales website.",
    })


@app.post("/api/run")
async def run_script(
    script: str = Form(...),
    file_id: Optional[str] = Form(None),
    file_suffix: Optional[str] = Form(None),
    cookies: str = Form(default=""),
    params: str = Form(default="{}"),
) -> JSONResponse:
    excel_path: Optional[str] = None
    if file_id:
        suffix = file_suffix or ".xlsx"
        candidate = UPLOADS_DIR / f"{file_id}{suffix}"
        if not candidate.exists():
            for ext in (".xlsx", ".xls", ".csv"):
                candidate = UPLOADS_DIR / f"{file_id}{ext}"
                if candidate.exists():
                    break
            else:
                raise HTTPException(status_code=404, detail="Uploaded file not found. Please re-upload.")
        excel_path = str(candidate)

    try:
        extra_params = json.loads(params) if params.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid params JSON: {exc}") from exc

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "script": script,
            "log_queue": queue.Queue(),
            "logs": [],
        }

    thread = threading.Thread(
        target=_run_script_thread,
        args=(job_id, script, excel_path, cookies, extra_params),
        daemon=True,
    )
    thread.start()

    return JSONResponse({"job_id": job_id})


@app.get("/api/stream/{job_id}")
async def stream_logs(job_id: str) -> StreamingResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        log_q: queue.Queue = jobs[job_id]["log_queue"]

        with jobs_lock:
            already = list(jobs[job_id]["logs"])
        for line in already:
            yield f"data: {line}\n\n"

        while True:
            try:
                line = log_q.get(timeout=0.2)
                if line is None:
                    status = jobs[job_id]["status"]
                    yield f"event: done\ndata: {status}\n\n"
                    return
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"
                await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    with jobs_lock:
        job = jobs[job_id]
        return JSONResponse({
            "job_id": job_id,
            "status": job["status"],
            "script": job["script"],
            "logs": list(job["logs"]),
        })


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
