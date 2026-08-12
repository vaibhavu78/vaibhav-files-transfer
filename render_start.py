# -*- coding: utf-8 -*-
"""
Render entry point.
-------------------
Render runs:  python render_start.py
This starts the Flask-SocketIO server bound to 0.0.0.0:$PORT.

Strategy:
  - Try eventlet async mode first (best WebSocket support for the admin panel
    real-time notifications). This is what we want on Render.
  - If eventlet import/monkey-patch fails, fall back to threading mode
    (still works; admin panel uses long-polling fallback).

PERMANENT_DOMAIN must be set as an env var on Render (the onrender.com URL
that Render assigns) so the QR code points to the permanent public domain.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))            # FileTransfer_Upgraded/
sys.path.insert(0, str(BASE / "SOURCE")) # FileTransfer_Upgraded/SOURCE/

# Try eventlet first; if it fails we still continue with threading.
_USE_EVENTLET = False
try:
    import eventlet  # noqa: E402
    eventlet.monkey_patch()
    _USE_EVENTLET = True
    print("[render_start] eventlet async mode: ON")
except Exception as e:
    print("[render_start] eventlet unavailable, using threading mode:", e)

import app as server_app  # noqa: E402

# Ensure PERMANENT_DOMAIN is picked up
domain = os.environ.get("PERMANENT_DOMAIN", "").strip().rstrip("/")
if domain:
    server_app.PERMANENT_DOMAIN = domain
    server_app.IS_DEPLOYED = True

port = int(os.environ.get("PORT", "10000"))
host = "0.0.0.0"

# Generate the permanent QR on boot
try:
    server_app.ensure_permanent_qr()
    print("[render_start] QR generated for:", server_app._permanent_url())
except Exception as e:
    print("[render_start] QR generation warning:", e)

srv = server_app.create_server()
sio = getattr(server_app, "socketio", None)

if sio is not None and _USE_EVENTLET:
    print(f"[render_start] Starting Flask-SocketIO (eventlet) on {host}:{port}")
    sio.run(srv, host=host, port=port, allow_unsafe_werkzeug=True)
elif sio is not None:
    print(f"[render_start] Starting Flask-SocketIO (threading) on {host}:{port}")
    sio.run(srv, host=host, port=port, allow_unsafe_werkzeug=True)
else:
    from werkzeug.serving import make_server
    print(f"[render_start] Starting Flask (werkzeug) on {host}:{port}")
    make_server(host, port, srv, threaded=True).serve_forever()
