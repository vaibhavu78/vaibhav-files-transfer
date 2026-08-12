# -*- coding: utf-8 -*-
"""
Vaibhav Files Transfer — Backend
=================================
Flask + Flask-SocketIO backend that:

  * Serves a clean, minimalist mobile upload UI (no branding shown to sender).
  * Accepts UNLIMITED-size file uploads via chunked streaming + resume support
    (4 MB chunks; failed chunks can be retried; uploads resume after network
    drops because the server keeps received chunks on disk and the client asks
    /upload/status before re-sending).
  * Generates ONE PERMANENT QR code pointing to the deployed public domain
    (env PERMANENT_DOMAIN) so the QR never changes regardless of network.
    A `.url` sidecar file tracks the embedded URL (NO pyzbar — works on
    Windows where zbar's native DLL cannot be pip-installed).
  * Real-time admin notifications via WebSocket (sound + animation + badge)
    the instant a file arrives, plus live upload-progress bars.
  * Tracks connected users with rich device info (name, browser, platform, OS,
    online/offline status, connection time).
  * Optional Supabase persistence for history / settings / users / devices
    (gracefully degrades to local JSON files if Supabase is not configured).
  * Keeps every existing route (chunk upload, status, complete, admin status,
    inbox, inbox search, clients, devices, config, block/unblock, save_to,
    preview, download, delete, clear history, QR refresh/download) intact.

License: MIT
"""

import os
import io
import json
import socket
import time
import uuid
import shutil
import threading
import subprocess
import sys
import platform
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask, request, render_template, render_template_string, jsonify,
    send_from_directory, abort, Response, stream_with_context, session,
    redirect, url_for
)

# Flask-SocketIO for real-time admin notifications + upload progress
try:
    from flask_socketio import SocketIO, emit
    _HAS_SOCKETIO = True
except Exception:  # pragma: no cover
    _HAS_SOCKETIO = False

    class _DummyEmit:
        def __call__(self, *a, **k):
            return False

    emit = _DummyEmit()

# ----------------------------------------------------------------------
# Configuration / paths
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

# Frozen by PyInstaller -> templates bundled inside _MEIPASS, runtime data
# lives NEXT TO the EXE.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    EXE_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = BASE_DIR
    EXE_DIR = BASE_DIR

TEMPLATE_DIR = BUNDLE_DIR / "templates"
STATIC_DIR = BUNDLE_DIR / "static"

UPLOAD_DIR = EXE_DIR / "received_files"
QR_DIR = EXE_DIR / "qr_cache"
META_FILE = EXE_DIR / "inbox_meta.json"
CONFIG_FILE = EXE_DIR / "server_config.json"
BLOCKED_FILE = EXE_DIR / "blocked_clients.json"
DEVICES_FILE = EXE_DIR / "connected_devices.json"
SESSIONS_FILE = EXE_DIR / "upload_sessions.json"  # for resume support

CHUNK_DIR = UPLOAD_DIR / ".chunks"

DEFAULT_PORT = int(os.environ.get("PORT", 8080))
MAX_CONTENT_LENGTH = None  # unlimited - we stream, never load fully into RAM

# ----------------------------------------------------------------------
# Environment: permanent domain + admin access
# ----------------------------------------------------------------------

# The permanent public URL of the deployed backend.
# Example: https://file-transfer-system-xxxx.onrender.com
# When set, the QR code always points here (stable, works from ANY network).
PERMANENT_DOMAIN = os.environ.get("PERMANENT_DOMAIN", "").strip().rstrip("/")

# Admin password protects the admin panel in BOTH local and deployed mode.
# Default is "radha". Override with the ADMIN_PASSWORD env var on Render.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "radha").strip()
# Legacy optional extra secret (kept for backwards compatibility on Render).
ADMIN_ACCESS_KEY = os.environ.get("ADMIN_ACCESS_KEY", "").strip()

# Supabase (optional). If not set, local JSON persistence is used.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

# Whether admin is restricted to loopback. On a deployed public domain we
# CANNOT restrict to loopback (the request comes from Render's proxy). Instead
# we gate with ADMIN_PASSWORD (always) + ADMIN_ACCESS_KEY (deployed, optional).
IS_DEPLOYED = bool(PERMANENT_DOMAIN)

# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR) if STATIC_DIR.exists() else None,
)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
app.secret_key = os.environ.get("SECRET_KEY", "file-transfer-secret-" + uuid.uuid4().hex)

# SocketIO with eventlet async mode when available (needed on Render).
# In standalone EXE mode we fall back to threading mode.
_async_mode = "eventlet" if _HAS_SOCKETIO else "threading"
try:
    import eventlet  # noqa: F401
    _async_mode = "eventlet"
except Exception:
    _async_mode = "threading"

socketio = None
if _HAS_SOCKETIO:
    try:
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode,
                            ping_timeout=60, ping_interval=25)
    except Exception:
        try:
            socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
        except Exception:
            socketio = None


def force_threading_mode():
    """Recreate the SocketIO server in pure threading mode.

    Called by the launcher when eventlet causes problems on Windows / certain
    Python builds.  Safe to call any time before the server is started.
    Returns True if the switch succeeded (socketio is now threading-based).
    """
    global socketio, _async_mode
    if not _HAS_SOCKETIO:
        return False
    try:
        socketio = SocketIO(app, cors_allowed_origins="*",
                            async_mode="threading",
                            ping_timeout=60, ping_interval=25)
        _async_mode = "threading"
        return True
    except Exception:
        return False

# Ensure runtime directories exist
for d in (UPLOAD_DIR, QR_DIR, CHUNK_DIR, EXE_DIR / "downloads"):
    d.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# Helpers - network / config / persistence
# ----------------------------------------------------------------------

def get_lan_ip() -> str:
    """Return the most likely LAN IPv4 address of this machine."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        if s:
            s.close()
    return ip


def load_json(path, default):
    try:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_config() -> dict:
    cfg = load_json(CONFIG_FILE, {})
    if not cfg:
        cfg = {
            "host_ip": get_lan_ip(),
            "port": DEFAULT_PORT,
            "auto_detect_ip": True,
            "custom_download_dir": str(EXE_DIR / "downloads"),
        }
        save_json(CONFIG_FILE, cfg)
    # Re-detect LAN IP each launch if auto mode (used only in local EXE mode)
    if cfg.get("auto_detect_ip", True) and not IS_DEPLOYED:
        cfg["host_ip"] = get_lan_ip()
        save_json(CONFIG_FILE, cfg)
    return cfg


def save_config(cfg: dict):
    save_json(CONFIG_FILE, cfg)


# ---- Persistence backends --------------------------------------------

_meta_lock = threading.Lock()
_blocked_lock = threading.Lock()
_devices_lock = threading.Lock()
_sessions_lock = threading.Lock()
_clients_lock = threading.Lock()

_connected_clients = {}   # ip -> {"last_seen": ts, "device": str, ...}
_active_sessions = {}     # upload_id -> {received: set, total: int, filename, ...}


def load_inbox() -> list:
    return load_json(META_FILE, [])


def save_inbox(data: list):
    save_json(META_FILE, data)
    _supabase_sync_history(data)


def load_blocked() -> list:
    return load_json(BLOCKED_FILE, [])


def save_blocked(data: list):
    save_json(BLOCKED_FILE, data)


def load_devices() -> list:
    return load_json(DEVICES_FILE, [])


def save_devices(data: list):
    save_json(DEVICES_FILE, data)


def load_sessions() -> dict:
    return load_json(SESSIONS_FILE, {})


def save_sessions(data: dict):
    save_json(SESSIONS_FILE, data)


def is_blocked(ip: str) -> bool:
    return ip in load_blocked()


# ----------------------------------------------------------------------
# Optional Supabase integration (graceful no-op if not configured)
# ----------------------------------------------------------------------

_supabase = None
_supabase_lock = threading.Lock()


def _get_supabase():
    global _supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    if _supabase is not None:
        return _supabase
    with _supabase_lock:
        try:
            from supabase import create_client
            _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception:
            _supabase = None
    return _supabase


def _supabase_sync_history(inbox: list):
    """Best-effort push of history to Supabase. Never raises."""
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table("file_history").delete().neq("id", "0").execute()
        rows = [{
            "id": f.get("id", uuid.uuid4().hex),
            "name": f.get("name", ""),
            "size": f.get("size", 0),
            "sender_ip": f.get("sender_ip", ""),
            "device": f.get("device", ""),
            "time": f.get("time", ""),
        } for f in inbox[:200]]
        if rows:
            sb.table("file_history").insert(rows).execute()
    except Exception:
        pass


# ----------------------------------------------------------------------
# QR code generation (PERMANENT when deployed)
# ----------------------------------------------------------------------

def _permanent_url() -> str:
    """The permanent public URL the QR code should always point to."""
    if PERMANENT_DOMAIN:
        return PERMANENT_DOMAIN
    # Local EXE fallback: LAN IP + port (still works on same Wi-Fi only).
    cfg = load_config()
    return f"http://{cfg.get('host_ip')}:{cfg.get('port')}/"


def generate_qr_png(text: str, filename: str = "qr.png") -> str:
    """Generate a QR code PNG and return its absolute path."""
    import qrcode
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    path = QR_DIR / filename
    img.save(str(path))
    return str(path)


def _read_qr_embedded_url(path) -> str:
    """Read the URL stored alongside an existing QR png (sidecar file).

    We don't try to decode the QR image with pyzbar because pyzbar needs a
    native zbar DLL that pip cannot install on Windows. Instead we always
    write a sidecar file next to the QR and read that — works everywhere.

    Primary sidecar: qr_cache/current.url  (per spec)
    Legacy fallback: qr_cache/qr.png.url   (backward compat)
    """
    try:
        primary = QR_DIR / "current.url"
        if primary.exists():
            return primary.read_text(errors="replace").strip()
    except Exception:
        pass
    try:
        sidecar = Path(str(path) + ".url")
        if sidecar.exists():
            return sidecar.read_text(errors="replace").strip()
    except Exception:
        pass
    return ""


def _write_qr_sidecar(url: str) -> None:
    """Write the encoded URL into the sidecar files so we can later detect
    QR changes without pyzbar.  Writes both current.url (spec) and the
    legacy qr.png.url for compatibility."""
    url = (url or "").strip()
    try:
        (QR_DIR / "current.url").write_text(url)
    except Exception:
        pass
    try:
        (Path(str(QR_DIR / "qr.png") + ".url")).write_text(url)
    except Exception:
        pass


def _normalize_url(url: str) -> str:
    """Validate + clean a URL for QR encoding.

    * Must start with http:// or https://
    * Strip accidental trailing / duplicate slashes
    * In production (PERMANENT_DOMAIN set) never allow localhost/127.0.0.1
    """
    url = (url or "").strip()
    if not url:
        return ""
    # collapse duplicate slashes after the protocol (keep the :// )
    m = re.match(r"^(https?://)(.*)$", url, re.IGNORECASE)
    if not m:
        return ""  # not a valid http/https url
    proto, rest = m.group(1).lower(), m.group(2)
    rest = re.sub(r"/{2,}", "/", rest)
    url = proto + rest
    # remove accidental trailing slash (but keep root path clean)
    url = url.rstrip("/") if url.endswith("/") and url.count("/") > 2 else url
    if not url.endswith("/"):
        url = url + "/"
    # production guard: never encode localhost in a deployed QR
    if IS_DEPLOYED and ("localhost" in url or "127.0.0.1" in url):
        return ""
    return url


def ensure_permanent_qr() -> str:
    """Make sure the permanent QR exists and points to the right URL.
    Always regenerates if the embedded URL differs from the current target
    (so a changed PERMANENT_DOMAIN at runtime reliably refreshes the QR)."""
    url = _normalize_url(_permanent_url())
    if not url:
        # fallback to raw _permanent_url() if normalization failed
        url = _permanent_url()
    p = QR_DIR / "qr.png"
    need = not p.exists()
    if not need:
        embedded = _read_qr_embedded_url(p)
        if embedded and embedded.rstrip("/") == url.rstrip("/"):
            need = False
        else:
            need = True  # URL changed -> regenerate
    if need:
        generate_qr_png(url, "qr.png")
        _write_qr_sidecar(url)
    return url


# ----------------------------------------------------------------------
# Device fingerprinting (rich connected-user info)
# ----------------------------------------------------------------------

_UA_BROWSER = [
    ("Edg", "Edge"), ("OPR", "Opera"), ("Chrome", "Chrome"),
    ("Firefox", "Firefox"), ("Safari", "Safari"), ("MSIE", "Internet Explorer"),
    ("Trident", "Internet Explorer"),
]
_UA_OS = [
    ("Windows NT 10", "Windows 10/11"), ("Windows NT 6.3", "Windows 8.1"),
    ("Windows NT 6.2", "Windows 8"), ("Windows NT 6.1", "Windows 7"),
    ("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"),
    ("iPad", "iPadOS"), ("Mac OS X", "macOS"), ("Mac OS", "macOS"),
    ("Linux", "Linux"), ("CrOS", "ChromeOS"),
]


def parse_user_agent(ua: str) -> dict:
    ua = ua or ""
    low = ua.lower()
    browser = "Unknown"
    for token, name in _UA_BROWSER:
        if token in ua:
            browser = name
            break
    os_name = "Unknown"
    for token, name in _UA_OS:
        if token in ua:
            os_name = name
            break
    platform = "Mobile" if any(k in low for k in ("mobile", "android", "iphone")) else "Desktop"
    # crude device model from UA
    model = "Unknown"
    m = re.search(r"(?:Android|iPhone)[^;]*;?\s*([^;)]+)", ua)
    if m:
        model = m.group(1).strip()[:40]
    return {
        "browser": browser,
        "os": os_name,
        "platform": platform,
        "model": model,
        "user_agent": ua[:200],
    }


def device_label(ua: str) -> str:
    info = parse_user_agent(ua)
    return f"{info['platform']} ({info['os']})"


def register_client(ip: str, ua: str = ""):
    """Track a connected client with rich device info + connection time."""
    if is_blocked(ip):
        return
    info = parse_user_agent(ua)
    now = time.time()
    with _clients_lock:
        existing = _connected_clients.get(ip, {})
        _connected_clients[ip] = {
            "last_seen": now,
            "connect_time": existing.get("connect_time", now),
            "device": device_label(ua),
            "browser": info["browser"],
            "os": info["os"],
            "platform": info["platform"],
            "model": info["model"],
            "user_agent": info["user_agent"],
        }


def touch_client(ip: str):
    with _clients_lock:
        if ip in _connected_clients:
            _connected_clients[ip]["last_seen"] = time.time()


def connected_clients_list() -> list:
    now = time.time()
    out = []
    with _clients_lock:
        for ip, v in _connected_clients.items():
            idle = int(now - v.get("last_seen", now))
            online = idle < 60  # online if seen within 60s
            out.append({
                "ip": ip,
                "device": v.get("device", "unknown"),
                "browser": v.get("browser", "Unknown"),
                "os": v.get("os", "Unknown"),
                "platform": v.get("platform", "Unknown"),
                "model": v.get("model", "Unknown"),
                "connect_time": datetime.fromtimestamp(
                    v.get("connect_time", now)).strftime("%Y-%m-%d %H:%M:%S"),
                "last_seen": datetime.fromtimestamp(
                    v.get("last_seen", now)).strftime("%H:%M:%S"),
                "idle_sec": idle,
                "online": online,
                "status": "Online" if online else "Offline",
            })
    return out


def human_size(num: float) -> str:
    num = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


# ----------------------------------------------------------------------
# Inline fallback HTML (only if templates/ is missing)
# ----------------------------------------------------------------------

_INLINE_INDEX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"/>
<title>Vaibhav Files Transfer</title><style>
:root{--bg:#0f172a;--accent:#6366f1;--accent2:#8b5cf6;--text:#e2e8f0;--muted:#94a3b8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px}
.wrap{width:100%;max-width:460px}.title{text-align:center;font-size:22px;font-weight:700;background:linear-gradient(90deg,#a5b4fc,#c4b5fd,#f0abfc);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;margin:0 0 6px}
.sub{text-align:center;color:var(--muted);font-size:13px;margin:0 0 22px}
.card{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:22px;padding:22px;backdrop-filter:blur(14px);box-shadow:0 20px 60px rgba(0,0,0,.35)}
.dz{border:2px dashed rgba(165,180,252,.45);border-radius:18px;padding:34px 18px;text-align:center;cursor:pointer;transition:.25s;background:rgba(255,255,255,.03)}
.dz:hover{border-color:var(--accent2);background:rgba(139,92,246,.12)}
#fi{display:none}.fl{margin-top:18px;display:flex;flex-direction:column;gap:10px}
</style></head><body><div class="wrap"><h1 class="title">Send Files</h1><p class="sub">Drag, drop, or tap to choose</p>
<div class="card"><div class="dz" id="dz">Tap or Drop files here<input type="file" id="fi" multiple/></div>
<div class="fl" id="fl"></div></div></div>
<script>window.location.href='/';</script></body></html>"""

_INLINE_ADMIN_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<title>Vaibhav Files Transfer — Admin</title><style>body{background:#0f172a;color:#e2e8f0;font-family:system-ui,sans-serif;margin:0;padding:20px}h1{color:#a5b4fc}a.lb{float:right;color:#94a3b8;font-size:13px;text-decoration:none}.card{background:#1e293b;border:1px solid #475569;border-radius:12px;padding:16px;margin:12px 0}img{max-width:240px;background:#fff;padding:8px;border-radius:8px}</style></head><body><h1>⚡ Vaibhav Files Transfer <a class="lb" href="/admin/logout">Logout</a></h1>
<div class="card"><h3>QR Code</h3><img src="/admin/qr.png" alt="QR"/></div>
<div class="card"><pre id="st">loading...</pre></div>
<script>fetch('/admin/status').then(r=>r.json()).then(s=>{document.getElementById('st').textContent='URL: '+s.qr_url+'\\nInbox: '+s.inbox_count});</script>
</body></html>"""


# ----------------------------------------------------------------------
# Admin access control
# ----------------------------------------------------------------------

def _admin_allowed() -> bool:
    """Determine whether the current request may access the admin panel.

    Auth model (unified for local + deployed):
      - ADMIN_PASSWORD is ALWAYS required (default "radha").
      - On the deployed public domain, ADMIN_ACCESS_KEY (if set) is an
        ADDITIONAL secret required on top of the password.
      - session["admin_ok"] == True means the user logged in correctly.
    """
    if session.get("admin_ok") is not True:
        return False
    # Deployed + extra key set: also require that key was validated at login.
    if IS_DEPLOYED and ADMIN_ACCESS_KEY:
        return session.get("admin_key_ok") is True
    return True


def _require_admin():
    """Guard for API/data routes — raises 403 if not authed."""
    if not _admin_allowed():
        abort(403)


# ----------------------------------------------------------------------
# Middleware - register/block clients
# ----------------------------------------------------------------------

@app.before_request
def _track_client():
    ip = request.remote_addr or ""
    ua = request.headers.get("User-Agent", "")
    # skip health check + static assets noise
    if request.path in ("//health", "/socket.io") or request.path.startswith("/socket.io/"):
        return
    if request.path == "/health":
        return
    if is_blocked(ip):
        abort(403, description="Your device has been blocked by the host.")
    register_client(ip, ua)


# ----------------------------------------------------------------------
# Health check (for Render)
# ----------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"ok": True, "status": "running", "deployed": IS_DEPLOYED})


# ----------------------------------------------------------------------
# User-facing routes (clean, no branding to the sender)
# ----------------------------------------------------------------------

@app.route("/")
def index():
    """Mobile upload page. Minimal - no server/admin info shown to sender."""
    try:
        return render_template("index.html")
    except Exception:
        return Response(_INLINE_INDEX_HTML, mimetype="text/html; charset=utf-8")


@app.route("/admin")
@app.route("/admin/")
def admin_panel():
    """Admin control panel. Always gated by ADMIN_PASSWORD login."""
    if not _admin_allowed():
        return redirect(url_for("admin_login"))
    try:
        return render_template("admin.html")
    except Exception:
        return Response(_INLINE_ADMIN_HTML, mimetype="text/html; charset=utf-8")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Password-based login for the admin panel.

    Always requires ADMIN_PASSWORD (default "radha").
    On the deployed public domain, also requires ADMIN_ACCESS_KEY if set.
    """
    if _admin_allowed():
        return redirect(url_for("admin_panel"))
    error = None
    if request.method == "POST":
        pwd = (request.form.get("password") or "").strip()
        key = (request.form.get("key") or "").strip()
        ok = (pwd == ADMIN_PASSWORD)
        if IS_DEPLOYED and ADMIN_ACCESS_KEY:
            ok = ok and (key == ADMIN_ACCESS_KEY)
        if ok:
            session["admin_ok"] = True
            if IS_DEPLOYED and ADMIN_ACCESS_KEY:
                session["admin_key_ok"] = True
            return redirect(url_for("admin_panel"))
        error = "गलत पासवर्ड (Wrong password)"
    return render_template_string(_LOGIN_HTML, error=error,
                                  need_key=bool(IS_DEPLOYED and ADMIN_ACCESS_KEY))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    session.pop("admin_key_ok", None)
    return redirect(url_for("admin_login"))


_LOGIN_HTML = """<!DOCTYPE html><html lang="hi"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Vaibhav Files Transfer — Admin Login</title><style>
:root{--bg:#0f172a;--panel:#1e293b;--text:#e2e8f0;--accent:#6366f1;--accent2:#8b5cf6;--err:#ef4444}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:linear-gradient(135deg,#0f172a,#1e1b4b);color:var(--text);font-family:system-ui,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:rgba(30,41,59,.85);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:32px;width:100%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.brand{font-size:22px;font-weight:800;margin:0 0 4px;background:linear-gradient(90deg,#a5b4fc,#c4b5fd);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;text-align:center}
.sub{color:#94a3b8;font-size:13px;margin:0 0 22px;text-align:center}
input{width:100%;padding:13px;background:#0f172a;border:1px solid #475569;border-radius:10px;color:var(--text);font-size:15px;margin-bottom:12px}
input:focus{outline:none;border-color:var(--accent)}
label{display:block;font-size:12px;color:#94a3b8;margin:0 0 5px 2px}
button{width:100%;padding:13px;border:none;border-radius:10px;background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-top:4px}
button:hover{filter:brightness(1.12)}
.err{color:var(--err);font-size:13px;margin-bottom:10px;text-align:center}
</style></head><body><form class="card" method="post">
<div class="brand">⚡ Vaibhav Files Transfer</div>
<p class="sub">Admin Panel — पासवर्ड डालें</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<label>पासवर्ड (Password)</label>
<input name="password" type="password" placeholder="राधा" autofocus/autocomplete="off"/>
{% if need_key %}<label>Access Key (extra security)</label>
<input name="key" type="password" placeholder="Access key"/>{% endif %}
<button type="submit">🔓 Unlock</button></form></body></html>"""


# ----------------------------------------------------------------------
# Chunked upload with RESUME support
# ----------------------------------------------------------------------

@app.route("/upload/chunk", methods=["POST"])
def upload_chunk():
    """
    Receive one chunk of a file. Supports resume: the client can call
    /upload/status first to learn which chunks are already on the server,
    then only upload the missing ones.
    """
    ip = request.remote_addr or ""
    if is_blocked(ip):
        abort(403)
    touch_client(ip)

    upload_id = request.form.get("upload_id") or uuid.uuid4().hex
    filename = os.path.basename(request.form.get("filename", "file"))
    chunk_index = int(request.form.get("chunk_index", "0"))
    total_chunks = int(request.form.get("total_chunks", "0"))

    if "chunk" not in request.files:
        return jsonify({"ok": False, "msg": "no chunk"}), 400

    session_dir = CHUNK_DIR / upload_id
    session_dir.mkdir(parents=True, exist_ok=True)

    chunk = request.files["chunk"]
    chunk_path = session_dir / f"{chunk_index:08d}.part"
    chunk.save(str(chunk_path))

    # Track session for resume + progress
    with _sessions_lock:
        sess = _active_sessions.setdefault(upload_id, {
            "received": set(),
            "total": total_chunks,
            "filename": filename,
            "ip": ip,
            "started": time.time(),
        })
        sess["received"].add(chunk_index)
        sess["total"] = max(sess.get("total", 0), total_chunks)
        received_count = len(sess["received"])
        total = sess["total"]

    # Emit real-time progress to admin
    try:
        emit("upload_progress", {
            "upload_id": upload_id,
            "filename": filename,
            "received": received_count,
            "total": total,
            "pct": round(received_count / max(1, total) * 100, 1),
            "ip": ip,
        }, namespace="/", broadcast=True)
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "upload_id": upload_id,
        "received": chunk_index,
        "received_count": received_count,
        "total": total,
    })


@app.route("/upload/status", methods=["POST"])
def upload_status():
    """Return which chunk indices already exist for an upload_id (resume support)."""
    ip = request.remote_addr or ""
    if is_blocked(ip):
        abort(403)
    data = request.get_json(force=True, silent=True) or {}
    upload_id = data.get("upload_id")
    if not upload_id:
        return jsonify({"ok": False, "msg": "missing upload_id"}), 400
    session_dir = CHUNK_DIR / upload_id
    received = []
    if session_dir.exists():
        received = sorted(
            int(p.stem) for p in session_dir.glob("*.part")
        )
    return jsonify({"ok": True, "upload_id": upload_id, "received": received})


@app.route("/upload/abort", methods=["POST"])
def upload_abort():
    """Cancel an in-progress upload and delete its partial chunks."""
    ip = request.remote_addr or ""
    if is_blocked(ip):
        abort(403)
    data = request.get_json(force=True, silent=True) or {}
    upload_id = data.get("upload_id")
    if not upload_id:
        return jsonify({"ok": False, "msg": "missing upload_id"}), 400
    session_dir = CHUNK_DIR / upload_id
    try:
        if session_dir.exists():
            shutil.rmtree(session_dir)
    except Exception:
        pass
    with _sessions_lock:
        _active_sessions.pop(upload_id, None)
    return jsonify({"ok": True})


@app.route("/upload/complete", methods=["POST"])
def upload_complete():
    """Reassemble chunks into the final file and add to inbox + notify admin."""
    ip = request.remote_addr or ""
    if is_blocked(ip):
        abort(403)
    touch_client(ip)

    data = request.get_json(force=True, silent=True) or {}
    upload_id = data.get("upload_id")
    filename = os.path.basename(data.get("filename", "file"))
    total_chunks = int(data.get("total_chunks", 0))
    device = data.get("device", "unknown")

    if not upload_id:
        return jsonify({"ok": False, "msg": "missing upload_id"}), 400

    session_dir = CHUNK_DIR / upload_id
    if not session_dir.exists():
        return jsonify({"ok": False, "msg": "no such upload session"}), 400

    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
    final_path = UPLOAD_DIR / safe_name

    with open(final_path, "wb") as out:
        for i in range(total_chunks):
            part = session_dir / f"{i:08d}.part"
            if not part.exists():
                return jsonify({"ok": False, "msg": f"missing chunk {i}"}), 400
            with open(part, "rb") as f_in:
                while True:
                    buf = f_in.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)

    try:
        shutil.rmtree(session_dir)
    except Exception:
        pass

    with _sessions_lock:
        _active_sessions.pop(upload_id, None)

    size = final_path.stat().st_size
    ua = request.headers.get("User-Agent", "")
    info = parse_user_agent(ua)
    meta_entry = {
        "id": upload_id,
        "name": filename,
        "stored_name": safe_name,
        "size": size,
        "size_human": human_size(size),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": time.time(),
        "sender_ip": ip,
        "device": device,
        "browser": info["browser"],
        "os": info["os"],
        "platform": info["platform"],
        "path": str(final_path),
    }

    # ---- AUTO-SAVE to configured download folder (local EXE mode) ----
    auto_msg = ""
    if not IS_DEPLOYED:
        try:
            cfg = load_config()
            dl_dir = cfg.get("custom_download_dir", "")
            if dl_dir:
                dl_path = Path(dl_dir)
                dl_path.mkdir(parents=True, exist_ok=True)
                auto_dest = dl_path / safe_name
                shutil.copy2(str(final_path), str(auto_dest))
                auto_msg = f" Auto-saved to {dl_dir}."
                meta_entry["auto_saved_to"] = str(auto_dest)
            else:
                auto_msg = " (No download folder configured - set one in Network Settings.)"
        except Exception as e:
            auto_msg = f" (Auto-save skipped: {e})"

    with _meta_lock:
        inbox = load_inbox()
        inbox.insert(0, meta_entry)
        save_inbox(inbox)

    # ---- REAL-TIME ADMIN NOTIFICATION (sound + animation + badge) ----
    try:
        emit("new_file", {
            "file": meta_entry,
            "name": filename,
            "size_human": human_size(size),
            "time": meta_entry["time"],
            "sender_ip": ip,
            "device": device,
        }, namespace="/", broadcast=True)
    except Exception:
        pass

    return jsonify({"ok": True, "file": meta_entry, "auto_save": auto_msg.strip()})


# ----------------------------------------------------------------------
# Admin routes
# ----------------------------------------------------------------------

@app.route("/admin/status")
def admin_status():
    _require_admin()
    cfg = load_config()
    url = _permanent_url()
    return jsonify({
        "ip": cfg.get("host_ip"),
        "port": cfg.get("port"),
        "auto_detect": cfg.get("auto_detect_ip", True),
        "download_dir": cfg.get("custom_download_dir"),
        "qr_path": str(QR_DIR / "qr.png"),
        "qr_url": url,
        "permanent": IS_DEPLOYED,
        "deployed": IS_DEPLOYED,
        "inbox_count": len(load_inbox()),
        "blocked_count": len(load_blocked()),
        "online_clients": sum(1 for c in connected_clients_list() if c["online"]),
    })


@app.route("/admin/qr.png")
def admin_qr_png():
    _require_admin()
    ensure_permanent_qr()
    return send_from_directory(str(QR_DIR), "qr.png", mimetype="image/png")


@app.route("/admin/qr/refresh", methods=["POST"])
def admin_qr_refresh():
    """Force-regenerate the permanent QR (e.g. after PERMANENT_DOMAIN changed)."""
    _require_admin()
    url = _normalize_url(_permanent_url()) or _permanent_url()
    generate_qr_png(url, "qr.png")
    _write_qr_sidecar(url)
    return jsonify({"ok": True, "qr_url": url})


@app.route("/admin/qr/download")
def admin_qr_download():
    """Download the permanent QR as a PNG file."""
    _require_admin()
    ensure_permanent_qr()
    return send_from_directory(str(QR_DIR), "qr.png", as_attachment=True,
                               download_name="file-transfer-qr.png")


@app.route("/admin/link")
def admin_link():
    """Return the permanent link (for Copy Link / Open Link buttons)."""
    _require_admin()
    return jsonify({"ok": True, "link": _permanent_url(),
                    "permanent": IS_DEPLOYED})


@app.route("/admin/inbox")
def admin_inbox():
    _require_admin()
    return jsonify(load_inbox())


@app.route("/admin/inbox/search")
def admin_inbox_search():
    """Search / filter / sort the inbox history."""
    _require_admin()
    inbox = load_inbox()
    q = (request.args.get("q") or "").lower().strip()
    ftype = (request.args.get("type") or "all").lower()
    sort = (request.args.get("sort") or "newest").lower()

    if q:
        inbox = [f for f in inbox if q in (f.get("name", "").lower()) or
                 q in (f.get("sender_ip", "").lower()) or
                 q in (f.get("device", "").lower())]

    if ftype != "all":
        def match(f):
            ext = Path(f.get("name", "")).suffix.lower().lstrip(".")
            cats = {
                "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "tif", "tiff"},
                "video": {"mp4", "webm", "mov", "avi", "mkv", "m4v", "flv"},
                "audio": {"mp3", "wav", "ogg", "m4a", "aac", "flac"},
                "pdf": {"pdf"},
                "doc": {"doc", "docx", "txt", "rtf", "odt"},
                "excel": {"xls", "xlsx", "csv", "ods"},
                "apk": {"apk"},
                "archive": {"zip", "rar", "7z", "tar", "gz"},
            }
            for cat, exts in cats.items():
                if ftype == cat and ext in exts:
                    return True
            return False
        inbox = [f for f in inbox if match(f)]

    if sort == "oldest":
        inbox = sorted(inbox, key=lambda f: f.get("timestamp", 0))
    elif sort == "largest":
        inbox = sorted(inbox, key=lambda f: f.get("size", 0), reverse=True)
    elif sort == "smallest":
        inbox = sorted(inbox, key=lambda f: f.get("size", 0))
    elif sort == "name":
        inbox = sorted(inbox, key=lambda f: f.get("name", "").lower())
    else:  # newest
        inbox = sorted(inbox, key=lambda f: f.get("timestamp", 0), reverse=True)

    return jsonify(inbox)


@app.route("/admin/file/<stored_name>")
def admin_download(stored_name):
    _require_admin()
    return send_from_directory(str(UPLOAD_DIR), stored_name, as_attachment=True)


@app.route("/admin/preview/<stored_name>")
def admin_preview(stored_name):
    _require_admin()
    return send_from_directory(str(UPLOAD_DIR), stored_name)


@app.route("/admin/delete", methods=["POST"])
def admin_delete():
    _require_admin()
    data = request.get_json(force=True, silent=True) or {}
    stored_name = data.get("stored_name")
    if not stored_name:
        return jsonify({"ok": False, "msg": "no stored_name"}), 400
    fp = UPLOAD_DIR / stored_name
    removed = False
    if fp.exists():
        try:
            fp.unlink()
            removed = True
        except Exception as e:
            return jsonify({"ok": False, "msg": str(e)}), 500
    with _meta_lock:
        inbox = load_inbox()
        inbox = [x for x in inbox if x.get("stored_name") != stored_name]
        save_inbox(inbox)
    return jsonify({"ok": True, "deleted": removed})


@app.route("/admin/save_to", methods=["POST"])
def admin_save_to():
    """Copy a received file to a custom directory (Save button)."""
    _require_admin()
    data = request.get_json(force=True, silent=True) or {}
    stored_name = data.get("stored_name")
    dest_dir = data.get("dest_dir")
    if not stored_name or not dest_dir:
        return jsonify({"ok": False, "msg": "missing params"}), 400
    src = UPLOAD_DIR / stored_name
    if not src.exists():
        return jsonify({"ok": False, "msg": "source missing"}), 404
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    inbox = load_inbox()
    orig = next((x for x in inbox if x.get("stored_name") == stored_name), {})
    dest = Path(dest_dir) / orig.get("name", stored_name)
    try:
        shutil.copy2(str(src), str(dest))
        return jsonify({"ok": True, "saved_to": str(dest)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route("/admin/clients")
def admin_clients():
    _require_admin()
    return jsonify({
        "clients": connected_clients_list(),
        "blocked": load_blocked(),
    })


@app.route("/admin/devices")
def admin_devices():
    """Connected users with full device info (richer than /clients)."""
    _require_admin()
    return jsonify({"devices": connected_clients_list()})


@app.route("/admin/block", methods=["POST"])
def admin_block():
    _require_admin()
    data = request.get_json(force=True, silent=True) or {}
    ip = data.get("ip")
    if not ip:
        return jsonify({"ok": False}), 400
    with _blocked_lock:
        blocked = load_blocked()
        if ip not in blocked:
            blocked.append(ip)
            save_blocked(blocked)
    with _clients_lock:
        _connected_clients.pop(ip, None)
    return jsonify({"ok": True, "blocked": blocked})


@app.route("/admin/unblock", methods=["POST"])
def admin_unblock():
    _require_admin()
    data = request.get_json(force=True, silent=True) or {}
    ip = data.get("ip")
    with _blocked_lock:
        blocked = load_blocked()
        if ip in blocked:
            blocked.remove(ip)
            save_blocked(blocked)
    return jsonify({"ok": True, "blocked": blocked})


@app.route("/admin/config", methods=["GET", "POST"])
def admin_config():
    _require_admin()
    if request.method == "GET":
        return jsonify(load_config())
    data = request.get_json(force=True, silent=True) or {}
    cfg = load_config()
    if "host_ip" in data:
        cfg["host_ip"] = data["host_ip"]
        cfg["auto_detect_ip"] = False
    if "port" in data:
        try:
            cfg["port"] = int(data["port"])
        except Exception:
            pass
    if "auto_detect_ip" in data:
        cfg["auto_detect_ip"] = bool(data["auto_detect_ip"])
    if "custom_download_dir" in data:
        cfg["custom_download_dir"] = data["custom_download_dir"]
    save_config(cfg)
    if not IS_DEPLOYED:
        url = _normalize_url(f"http://{cfg.get('host_ip')}:{cfg.get('port')}/")
        if url:
            generate_qr_png(url, "qr.png")
            _write_qr_sidecar(url)
    return jsonify({"ok": True, "config": cfg,
                    "note": "Restart the app to apply a new port or IP."})


@app.route("/admin/clear_history", methods=["POST"])
def admin_clear_history():
    """Clear the entire inbox history (files kept on disk unless delete_files=true)."""
    _require_admin()
    data = request.get_json(force=True, silent=True) or {}
    delete_files = bool(data.get("delete_files", False))
    if delete_files:
        for f in load_inbox():
            try:
                (UPLOAD_DIR / f.get("stored_name", "")).unlink(missing_ok=True)
            except Exception:
                pass
    with _meta_lock:
        save_inbox([])
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Socket.IO events
# ----------------------------------------------------------------------

def _on_connect():  # pragma: no cover
    try:
        emit("server_hello", {"msg": "connected", "deployed": IS_DEPLOYED})
    except Exception:
        pass


def _on_admin_join():  # pragma: no cover
    try:
        emit("admin_ready", {"inbox_count": len(load_inbox())})
    except Exception:
        pass


if socketio is not None:
    socketio.on("connect")(_on_connect)
    socketio.on("admin_join")(_on_admin_join)


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------

def create_server():
    """Pre-generate the permanent QR + return app for the WSGI server / launcher."""
    ensure_permanent_qr()
    return app


def run_standalone(host: str = None, port: int = None):
    """Run via SocketIO server (real-time notifications work)."""
    cfg = load_config()
    host = host or cfg.get("host_ip", get_lan_ip())
    port = port or cfg.get("port", DEFAULT_PORT)
    create_server()
    print(f"[FileTransfer] Serving on http://{host}:{port}/")
    print(f"[FileTransfer] Permanent QR URL: {_permanent_url()}")
    if socketio is not None:
        socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
    else:
        from werkzeug.serving import make_server
        make_server(host, port, app, threaded=True).serve_forever()


# Gunicorn entry: app is imported directly (see Procfile / render.yaml).
# When running under gunicorn+eventlet, SocketIO uses the eventlet worker.

if __name__ == "__main__":
    run_standalone()
