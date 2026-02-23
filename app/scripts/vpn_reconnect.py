"""
VPN Reconnect — kills any active SonicWall NetExtender session and reconnects.

.env format:
    VPN_SERVER=vpn.yourcompany.com:4433
    VPN_USERNAME=your_username
    VPN_PASSWORD=your_password
    VPN_DOMAIN=LocalDomain
"""

import subprocess
import time
from pathlib import Path


def _load_env(log):
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        raise RuntimeError(
            f".env file not found at {env_path}\n"
            "Create it from .env.example and fill in your VPN credentials."
        )

    env_vars = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip('"').strip("'")

    log(f"[INFO] Loaded credentials from {env_path}")
    return env_vars


def run(log, excel_path, cookies, params):
    try:
        env = _load_env(log)
    except RuntimeError as exc:
        log(f"[ERROR] {exc}")
        return

    server   = env.get("VPN_SERVER", "")
    username = env.get("VPN_USERNAME", "")
    password = env.get("VPN_PASSWORD", "")
    domain   = env.get("VPN_DOMAIN", "LocalDomain")

    missing = [name for name, val in [
        ("VPN_SERVER",   server),
        ("VPN_USERNAME", username),
        ("VPN_PASSWORD", password),
    ] if not val]

    if missing:
        log(f"[ERROR] Missing required .env variables: {', '.join(missing)}")
        return

    log("[INFO] Stopping any existing VPN session…")
    kill_result = subprocess.run(["pkill", "-f", "netExtender"], capture_output=True)
    if kill_result.returncode == 0:
        log("[INFO] Previous session terminated.")
        time.sleep(2)
    else:
        log("[INFO] No active session found.")

    log(f"[INFO] Connecting to {server} as '{username}' (domain: {domain})…")

    cmd = ["netExtender", "-s", server, "-u", username, "-p", password, "-d", domain]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except FileNotFoundError:
        log("[ERROR] 'netExtender' command not found.")
        log("[INFO]  https://www.sonicwall.com/products/remote-access/vpn-clients/")
        return
    except subprocess.TimeoutExpired:
        log("[ERROR] VPN connection timed out after 45 seconds.")
        return

    for line in (result.stdout or "").strip().splitlines():
        log(f"[INFO] {line}")
    for line in (result.stderr or "").strip().splitlines():
        log(f"[WARN] {line}")

    if result.returncode != 0:
        log(f"[ERROR] netExtender exited with code {result.returncode}.")
        return

    log("[INFO] Waiting 3 seconds for tunnel to stabilise…")
    time.sleep(3)
    log("[SUCCESS] VPN reconnected.")
