# -*- coding: utf-8 -*-
"""
File Transfer System - Launcher (Robust, Auto-Tunnel)
======================================================
Self-contained launcher that gives you a PUBLIC URL + QR code so that ANY
person, on ANY network (their own mobile data, a different WiFi, anywhere),
can scan the printed QR and send files straight to this laptop.

HOW IT WORKS (no accounts, no external services required):
  1. Starts the local Flask-SocketIO server on a free port.
  2. Automatically downloads the "cloudflared" tunnel binary for this OS
     (Windows / macOS / Linux) on first run.
  3. Starts a Cloudflare QUICK TUNNEL pointing at the local server.
  4. Reads the public https://*.trycloudflare.com URL from the tunnel log.
  5. Sets PERMANENT_DOMAIN = that public URL and regenerates the QR.
  6. Prints the public URL + QR + opens the admin panel in the browser.

The result: the admin panel + received files live on THIS laptop; the public
upload page is reachable from any network via the QR. Print the QR, stick it
on a wall, and any visitor can scan & send.

PERMANENCE NOTE:
  - Quick tunnel URLs change every time the launcher is (re)started.
    That's fine if you print a fresh QR each session, OR
  - For a STABLE permanent URL that never changes, run once with a free
    Cloudflare account using a NAMED tunnel (see CLOUDFLARE_NAMED_TUNNEL
    section below / the on-screen menu option 'P').

Flow in deployed (Render) mode (PERMANENT_DOMAIN already set in env):
  - QR -> https://your-domain.onrender.com/   (public upload page)
  - Admin panel -> /admin (gated by ADMIN_ACCESS_KEY if set)

Includes:
  * Automatic port fallback (8080 -> 8081 -> ... 8089) if a port is busy.
  * Pre-binding port check so we never silently fail.
  * Clear console output + automatic browser open of the admin panel.
  * stdio safety net: fixes NoneType.flush crash if console is missing.
  * Auto download of cloudflared for the correct platform.

License: MIT
"""

import os
import sys
import time
import socket
import json
import re
import shutil
import threading
import webbrowser
import subprocess
import platform
import urllib.request
from pathlib import Path


# ----------------------------------------------------------------------
# STDIO SAFETY NET
# ----------------------------------------------------------------------
def _fix_stdio():
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    except Exception:
        class _Null:
            def write(self, *a, **k): pass
            def flush(self, *a, **k): pass
            def read(self, *a, **k): return ""
            def close(self, *a, **k): pass
        if sys.stdout is None: sys.stdout = _Null()
        if sys.stderr is None: sys.stderr = _Null()

_fix_stdio()

def _safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        pass

def _safe_flush():
    try:
        if sys.stdout and hasattr(sys.stdout, "flush"):
            sys.stdout.flush()
    except Exception:
        pass


BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import app as server_app  # noqa: E402


# ----------------------------------------------------------------------
# CLOUDFLARED (quick tunnel) management
# ----------------------------------------------------------------------
CLOUDFLARED_VERSION = "2026.7.3"
CLOUDFLARED_DIR = BASE_DIR / ".cloudflared"
CLOUDFLARED_LOG = CLOUDFLARED_DIR / "tunnel.log"

# platform -> (asset_filename_substring, binary_name, url_template)
# We use the GitHub release assets for cloudflared.
def _cloudflared_asset_info():
    system = platform.system()
    machine = platform.machine().lower()
    base = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    if system == "Windows":
        arch = "amd64" if "64" in machine or "x86_64" in machine else "386"
        return ("cloudflared-windows-%s.exe" % arch,
                "cloudflared.exe",
                base + "cloudflared-windows-%s.exe" % arch)
    elif system == "Darwin":
        arch = "arm64" if "arm" in machine or "aarch64" in machine else "amd64"
        return ("cloudflared-darwin-%s.tgz" % arch,
                "cloudflared",
                base + "cloudflared-darwin-%s.tgz" % arch)
    else:  # Linux and friends
        if "arm" in machine or "aarch64" in machine:
            arch = "arm64"
        elif "64" in machine or "x86_64" in machine:
            arch = "amd64"
        else:
            arch = "386"
        return ("cloudflared-linux-%s" % arch,
                "cloudflared",
                base + "cloudflared-linux-%s" % arch)


def ensure_cloudflared() -> Path:
    """Download cloudflared for the current OS if not already present.
    Returns the path to the binary."""
    CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
    asset, binname, url = _cloudflared_asset_info()
    binpath = CLOUDFLARED_DIR / binname

    # If binary already exists and runs, skip download
    if binpath.exists():
        try:
            r = subprocess.run([str(binpath), "--version"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return binpath
        except Exception:
            pass

    _safe_print("  [tunnel] Downloading cloudflared for %s %s ..." % (
        platform.system(), platform.machine()))
    archive_path = CLOUDFLARED_DIR / asset

    # Download
    req = urllib.request.Request(url, headers={"User-Agent": "FileTransfer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, \
         open(archive_path, "wb") as out:
        shutil.copyfileobj(resp, out)

    # Extract if needed
    if asset.endswith(".tgz"):
        import tarfile
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(CLOUDFLARED_DIR)
        # find extracted binary named 'cloudflared'
        cand = CLOUDFLARED_DIR / "cloudflared"
        if cand.exists():
            binpath = cand
    else:
        # On Windows/Linux the downloaded file is the binary itself (or .exe)
        if not asset.endswith(".exe"):
            # linux: rename asset to binname
            shutil.move(str(archive_path), str(binpath))
        else:
            binpath = archive_path

    # chmod +x on non-Windows
    if platform.system() != "Windows":
        try:
            binpath.chmod(0o755)
        except Exception:
            pass

    # verify
    try:
        r = subprocess.run([str(binpath), "--version"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError("cloudflared --version failed: %s" % r.stderr)
        _safe_print("  [tunnel] cloudflared ready: %s" % r.stdout.strip())
    except Exception as e:
        _safe_print("  [tunnel] WARNING: cloudflared verification failed: %s" % e)

    return binpath


def start_quick_tunnel(local_port: int, timeout_s: int = 45):
    """Start a Cloudflare quick tunnel -> http://localhost:local_port.
    Returns (proc, public_url) or (None, None) on failure."""
    try:
        binpath = ensure_cloudflared()
    except Exception as e:
        _safe_print("  [tunnel] Could not obtain cloudflared: %s" % e)
        return None, None

    # Clear old log
    try:
        CLOUDFLARED_LOG.write_text("")
    except Exception:
        pass

    cmd = [str(binpath), "tunnel", "--url",
           "http://localhost:%d" % local_port,
           "--no-autoupdate"]
    try:
        logf = open(CLOUDFLARED_LOG, "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                cwd=str(CLOUDFLARED_DIR))
    except Exception as e:
        _safe_print("  [tunnel] Failed to start tunnel: %s" % e)
        return None, None

    # Wait for the trycloudflare.com URL to appear in the log
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + timeout_s
    public_url = None
    while time.time() < deadline:
        if proc.poll() is not None:
            _safe_print("  [tunnel] cloudflared exited early. See log:")
            try:
                _safe_print(CLOUDFLARED_LOG.read_text()[-1000:])
            except Exception:
                pass
            return None, None
        try:
            text = CLOUDFLARED_LOG.read_text(errors="replace")
        except Exception:
            text = ""
        m = pattern.search(text)
        if m:
            public_url = m.group(0)
            break
        time.sleep(0.5)

    if not public_url:
        _safe_print("  [tunnel] Timed out waiting for public URL. See log:")
        try:
            _safe_print(CLOUDFLARED_LOG.read_text()[-1000:])
        except Exception:
            pass
        return None, None

    return proc, public_url


# ----------------------------------------------------------------------
# port helpers
# ----------------------------------------------------------------------
def port_in_use(host: str, port: int) -> bool:
    """Return True if something is actively LISTENING on (host, port).

    Uses a real TCP connect (not a bind-test) so it works reliably on
    Windows where SO_REUSEADDR lets a second bind succeed even when a
    server is already bound to 0.0.0.0.  A successful connect proves the
    server is actually up and accepting connections.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            s.connect((host, port))
        return True
    except (OSError, socket.timeout):
        return False


def find_free_port(host: str, start: int, count: int = 10):
    """Find a port that is NOT currently listening (bind-test is fine here
    because we just want to know which ports are free to bind to next)."""
    for p in range(start, start + count):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, p))
            return p
        except OSError:
            continue
    return None


def open_browser(url: str, delay: float = 2.5):
    time.sleep(delay)
    try:
        webbrowser.open(url, new=2)
    except Exception:
        try:
            if platform.system() == "Windows":
                os.startfile(url)  # type: ignore
            elif platform.system() == "Darwin":
                subprocess.run(["open", url])
            else:
                subprocess.run(["xdg-open", url])
        except Exception:
            pass


# ----------------------------------------------------------------------
# pretty QR printer
# ----------------------------------------------------------------------
def print_ascii_qr(url: str):
    """Render the QR as block characters in the console if possible."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1, box_size=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        # build a boolean matrix
        m = qr.get_matrix()
        lines = []
        for row in m:
            line = "".join("  " if c else "██" for c in row)
            lines.append(line)
        _safe_print("\n".join(lines))
    except Exception as e:
        _safe_print("  (ASCII QR unavailable: %s)" % e)


def _win_msg(title, msg, flags=0x40):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
    except Exception:
        pass


def main():
    # If PERMANENT_DOMAIN is already set (e.g. deployed on Render), use legacy flow
    pre_set_domain = os.environ.get("PERMANENT_DOMAIN", "").strip().rstrip("/")
    if pre_set_domain:
        _run_deployed_mode(pre_set_domain)
        return

    # Otherwise: local + auto tunnel mode
    _run_local_tunnel_mode()


def _run_deployed_mode(permanent_domain: str):
    cfg = server_app.load_config()
    requested_port = int(os.environ.get("PORT", cfg.get("port", 8080)))

    # On Render, bind to 0.0.0.0:$PORT
    bind_host = "0.0.0.0"
    port = requested_port

    _safe_print("=" * 64)
    _safe_print("          FILE TRANSFER SYSTEM (DEPLOYED)")
    _safe_print("=" * 64)
    _safe_print("  Permanent QR URL: %s" % permanent_domain)
    _safe_print("  (Senders on ANY network scan this QR -> upload page)")
    _safe_print("=" * 64)
    _safe_flush()

    try:
        server_app.ensure_permanent_qr()
    except Exception:
        pass

    try:
        srv_app = server_app.create_server()
    except Exception as e:
        _safe_print("  [ERROR] Could not build server app: %s" % e)
        return

    sio = getattr(server_app, "socketio", None)
    err_bag = []
    _serve(srv_app, sio, bind_host, port, err_bag)


def _run_local_tunnel_mode():
    cfg = server_app.load_config()
    requested_port = int(cfg.get("port", 8080))

    bind_host = "0.0.0.0"
    port = find_free_port(bind_host, requested_port)
    if port is None:
        _safe_print("  [ERROR] No free port found between %d and %d" % (
            requested_port, requested_port + 9))
        if platform.system() == "Windows":
            _win_msg("File Transfer - Port Error",
                     "No free port found between %d and %d.\n"
                     "Please close the program using that port or change the port in settings."
                     % (requested_port, requested_port + 9), 0x10)
        return
    if port != requested_port:
        cfg["port"] = port
        try: server_app.save_config(cfg)
        except Exception: pass

    _safe_print("=" * 64)
    _safe_print("          FILE TRANSFER SYSTEM")
    _safe_print("   (Auto public tunnel - works from ANY network)")
    _safe_print("=" * 64)
    _safe_print("  >> Local server starting on port %d ..." % port)
    _safe_flush()

    # ---- Force threading mode for local launcher (more reliable on Windows) ----
    # eventlet can crash sio.run() on some Windows / Python builds; the local
    # launcher doesn't need eventlet's scaling, so we use the rock-solid
    # threaded Werkzeug server.  (Deployed/Render mode still uses eventlet.)
    try:
        if hasattr(server_app, "force_threading_mode"):
            if server_app.force_threading_mode():
                _safe_print("  [ok] Using threaded server mode (best for Windows).")
    except Exception as e:
        _safe_print("  [warn] Could not switch to threading mode: %s" % e)
    _safe_flush()

    # ---- Build the server app (generates QR too) ----
    try:
        srv_app = server_app.create_server()
    except Exception as e:
        _safe_print("  [ERROR] Could not build server app: %s" % e)
        try:
            import traceback
            _safe_print(traceback.format_exc())
        except Exception:
            pass
        return

    sio = getattr(server_app, "socketio", None)
    err_bag = []
    server_thread = threading.Thread(
        target=_serve, args=(srv_app, sio, bind_host, port, err_bag), daemon=True)
    server_thread.start()
    # Wait for the server to actually accept connections (retry for up to ~6s).
    # Using a connect-test instead of a single sleep so we don't false-negative
    # on slow machines or Windows where bind != listening yet.
    started = False
    for _ in range(12):
        time.sleep(0.5)
        if port_in_use("127.0.0.1", port):
            started = True
            break
        if err_bag:
            break  # server crashed, no point waiting
    if not started:
        _safe_print("  [ERROR] Local server did not start.")
        if err_bag:
            _safe_print("  [ERROR] Reason: %s" % err_bag[0])
        else:
            _safe_print("  [HINT] Common causes:")
            _safe_print("         - Antivirus / Windows Defender blocking the port")
            _safe_print("         - Another app using port %d (close it or change port)" % port)
            _safe_print("         - Python firewall prompt — click 'Allow access'")
        return
    _safe_print("  [OK] Local server LIVE on 127.0.0.1:%d" % port)

    # ---- Start Cloudflare quick tunnel ----
    _safe_print("  [tunnel] Creating public HTTPS URL (no account needed) ...")
    _safe_flush()
    proc, public_url = start_quick_tunnel(port)
    if not public_url:
        _safe_print("\n  [!] Tunnel could not be created automatically.")
        _safe_print("      The system still works on your local network:")
        lan_ip = server_app.get_lan_ip()
        _safe_print("      -> http://%s:%d  (same WiFi only)" % (lan_ip, port))
        public_url = "http://%s:%d" % (lan_ip, port)

    # ---- Set PERMANENT_DOMAIN and regenerate QR ----
    os.environ["PERMANENT_DOMAIN"] = public_url
    server_app.PERMANENT_DOMAIN = public_url
    server_app.IS_DEPLOYED = True
    try:
        server_app.ensure_permanent_qr()
    except Exception as e:
        _safe_print("  [warn] QR regen failed: %s" % e)

    admin_url = "http://127.0.0.1:%d/admin" % port
    qr_url = server_app._permanent_url()

    _safe_print("=" * 64)
    _safe_print("  ★ PUBLIC URL (anyone, any network): %s" % qr_url)
    _safe_print("  ★ ADMIN PANEL (this laptop):        %s" % admin_url)
    _safe_print("  ★ RECEIVED FILES FOLDER:             %s" % server_app.UPLOAD_DIR)
    _safe_print("=" * 64)
    _safe_print("\n  Scan this QR with any phone camera:")
    print_ascii_qr(qr_url)
    _safe_print("\n  >>> PRINT the QR (admin panel -> QR -> Download) and stick it on a wall.")
    _safe_print("  >>> Keep this window OPEN while the system is running.")
    _safe_print("  >>> Press Ctrl+C to stop.")
    _safe_print("=" * 64)
    _safe_flush()

    threading.Thread(target=open_browser, args=(admin_url,), daemon=True).start()

    # ---- Keep alive until interrupted ----
    try:
        while True:
            time.sleep(2)
            # If tunnel died, warn once
            if proc and proc.poll() is not None:
                _safe_print("  [!] Public tunnel stopped. Files still work on local network.")
                _safe_print("      Restart the program to get a new public URL + QR.")
                proc = None
    except KeyboardInterrupt:
        _safe_print("\n  Shutting down...")
    finally:
        if proc:
            try: proc.terminate()
            except Exception: pass


def _serve(srv_app, sio, bind_host, port, err_bag=None):
    """Run the WSGI server in a background thread.

    err_bag: optional list whose first element is set to the exception if the
             server crashes — so the caller can show the REAL error instead of
             the useless "Local server did not start".
    """
    def _record(exc):
        if err_bag is not None:
            err_bag.append(exc)
        try:
            import traceback
            _safe_print("  [ERROR] Server thread crashed:")
            _safe_print(traceback.format_exc())
        except Exception:
            pass

    # First attempt: try socketio.run (eventlet or threading, whichever was set)
    if sio is not None:
        try:
            sio.run(srv_app, host=bind_host, port=port,
                    allow_unsafe_werkzeug=True)
            return
        except SystemExit:
            return
        except Exception as e:
            _safe_print("  [warn] socketio.run failed (%s) — retrying with plain Werkzeug (threading) ..." % type(e).__name__)
            _safe_flush()

    # Fallback: pure Werkzeug threaded server (always works, no eventlet)
    try:
        from werkzeug.serving import make_server
        make_server(bind_host, port, srv_app, threaded=True).serve_forever()
    except SystemExit:
        return
    except Exception as e:
        _record(e)


if __name__ == "__main__":
    main()
