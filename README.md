# ⚡ Vaibhav Files Transfer — Any-Network File Transfer & Management

A **production-ready** file transfer system. People scan **one permanent QR code** and upload files of **any size** (MB to GB) from **any network** — Wi-Fi, mobile data, hotspot, office, college, anywhere in the world. Files arrive instantly in the Admin Control Panel with real-time notifications (sound + animation + badge), preview, print, download, save, and delete.

> **No login for senders. No signup. No OTP. No account.** Scan → Open → Choose File → Send → Done.
> **Admin panel is password-protected** (default password: `radha`).

---

## 🚀 ONE CLICK RUN (Windows)

Just double-click **`RUN.bat`** — it does everything automatically:

1. Installs Python (if not found)
2. Installs Git (if not found)
3. Installs all libraries
4. Uploads to GitHub (asks for repo URL, or skip with ENTER)
5. Starts server + creates public URL + generates QR code
6. Opens admin panel in browser (password: `radha`)

No manual steps needed. Just click and wait. Keep the window open while it runs.

> For a quick local-only run without Git, double-click **`START.bat`** instead.

---

## ✨ Features

| Feature | Details |
|---------|---------|
| 🔒 **Admin password** | Default: `radha`. Protects admin panel. Senders don't need it. |
| 🖨 **Print button** | Print any received image/PDF/text directly from admin panel. |
| 📱 **Sender info display** | Each file shows device, OS, browser, IP address, and timestamp. |
| ⚡ **Vaibhav Files Transfer branding** | Name shown on admin panel, upload page, login page. |
| 🌐 **Works from ANY network** | Deployed on Render → permanent public URL. Senders on mobile data / different Wi-Fi / any internet can upload. 
| 🔔 **Real-time notifications** | WebSocket (Socket.IO) → instant sound + flash + badge when a file arrives. |
| 📦 **Chunked upload + resume** | 4 MB chunks. If a chunk fails, tap Retry and it resumes from where it stopped. |
| 📁 **Folder upload** | Pick a whole folder and upload every file inside it. |
| ♾️ **Unlimited file size** | GB-sized files supported via streaming chunked uploads. |
| 👥 **Connected Users panel** | Device name, browser, platform, OS, online/offline, connection time. |
| 🟢 **Online/offline status** | Live status per connected device (refreshes every few seconds). |
| 🚫 **Block/unblock clients** | Block a sender IP — they can no longer upload. |
| 👁 **File preview** | Images, PDF, text, video, audio — all previewable in a modal. |
| ⬇ **Download / Save To** | Download any file, or Save To a custom folder. |
| 🔍 **History search / filter / sort** | Search by name/IP/device, filter by type, sort by date/size/name. |
| 🗑 **Delete + Clear history** | Delete single files or clear the whole inbox. |
| 📇 **QR actions** | Download QR, Copy Link, Refresh QR, Open Link. |
| 🔗 **Public URL display** | The permanent public URL is shown in the admin panel. |
| 💾 **Persistent JSON storage** | Inbox, blocked IPs, config, devices saved as JSON files. |
| 🗄 **Optional Supabase** | Set `SUPABASE_URL` + `SUPABASE_KEY` to sync history to Supabase. |

---

## 📦 Project Structure

```
Vaibhav Files Transfer/
├── RUN.bat              ← ONE CLICK: git + install + run (double-click this!)
├── START.bat            ← Local run only (install + server + tunnel)
├── HOW_TO_RUN.txt       ← Simple text guide (Hindi + English)
├── README.md            ← This file
├── requirements.txt     ← Python libraries (no pyzbar — works on Windows!)
├── render.yaml          ← Render.com config (includes ADMIN_PASSWORD=radha)
├── render_start.py      ← Render.com entry point
├── Procfile             ← Render.com start command
├── runtime.txt          ← Python version for Render
├── FileTransfer.spec    ← PyInstaller spec (build Windows EXE)
├── .gitignore
└── SOURCE/
    ├── app.py           ← Backend server (Flask + SocketIO)
    ├── launcher.py      ← Auto tunnel + QR + browser opener
    ├── templates/
    │   ├── admin.html   ← Admin control panel (password protected)
    │   └── index.html   ← Sender upload page (public, no password)
    ├── qr_cache/        ← Generated QR PNG (+ .url sidecar)
    └── received_files/  ← Uploaded files land here
```

---

## 🌐 Free 24/7 Deployment Options

### Option A — Render.com (recommended, free, permanent URL)

1. Run `RUN.bat` → enter your GitHub repo URL → files upload to GitHub
   (or manually create a GitHub repo and push these files)
2. Go to **[render.com](https://render.com)** → Sign up with GitHub (free)
3. **New +** → **Web Service** → select your GitHub repo
4. Settings:
   - **Name:** `vaibhav-files-transfer`
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python render_start.py`
   - **Plan:** Free
5. **Create Web Service** → deploy (2–3 min)
6. Render gives you a URL like `https://vaibhav-files-transfer-xxxx.onrender.com`
7. Go to **Environment** tab → **Add Environment Variable**:
   - Key: `PERMANENT_DOMAIN`
   - Value: `https://vaibhav-files-transfer-xxxx.onrender.com`
     (your URL, with `https://`, NO trailing slash)
8. (Optional) Change `ADMIN_PASSWORD` to something other than `radha`.
9. Save → automatic re-deploy
10. Open `https://your-url/admin` → password `radha` → download QR → print

**Done! 24/7 online.** Laptop off? Still works. Anyone scans the QR from any
network → files arrive in your admin panel.

> Render free tier sleeps after 15 min of inactivity and wakes on the first
> request (≈30s). For always-on, upgrade to a paid plan ($7/mo) — or keep the
> admin panel tab open, which keeps it awake via the 4-second polling loop.

### Option B — Local + Cloudflare Quick Tunnel (no account, free)

Just run `RUN.bat` or `START.bat`. The launcher:
1. Starts the local server
2. Downloads `cloudflared` automatically
3. Creates a public `https://*.trycloudflare.com` URL (no account needed)
4. Generates the QR pointing to that URL and opens the admin panel

The tunnel URL changes every restart. For a **stable** local URL, use a free
Cloudflare account with a **Named Tunnel** (see the launcher menu / docs).

---

## 🔧 Admin Password

- Default: `radha`
- Change on Render: Environment → `ADMIN_PASSWORD` = your password
- Change locally: set the `ADMIN_PASSWORD` environment variable before running
- Logout button in top-right corner of admin panel
- Sender page (QR scan) is always public — no password needed

---

## 🖥 Build a Windows EXE (optional)

```bat
pip install pyinstaller
pyinstaller FileTransfer.spec
```
The standalone `FileTransfer.exe` will be in the `dist/` folder. Double-click
it — it bundles the templates, auto-starts the tunnel, and shows the QR.

---

## 🗄 Optional Supabase (cloud history sync)

If you want the file history mirrored to a cloud database:
1. Create a free project at **[supabase.com](https://supabase.com)**
2. Create a table `file_history` with columns:
   `id (text, pk)`, `name (text)`, `size (bigint)`, `sender_ip (text)`,
   `device (text)`, `time (text)`
3. On Render (or locally) set:
   - `SUPABASE_URL` = your project URL
   - `SUPABASE_KEY` = your anon/service key

If not set, the app uses local JSON files (always works).

---

## 🛠 Tech Stack

- **Python 3.11** + **Flask** + **Flask-SocketIO** + **Socket.IO**
- **qrcode** + **Pillow** (QR generation — no pyzbar, no native DLLs)
- **Vanilla HTML/CSS/JavaScript** (dark glassmorphism UI, indigo/cyan accents)
- **JSON storage** with optional **Supabase**
- **Cloudflare Quick Tunnel** for local public access
- **Render.com** for 24/7 deployment
- **PyInstaller** for Windows EXE

---

## ❓ Troubleshooting

| Problem | Solution |
|---------|----------|
| Python not found | RUN.bat auto-installs it. Wait. |
| pip install failed | Check internet. Or manual: `pip install flask flask-socketio eventlet qrcode pillow requests` |
| Port in use | Restart — auto-finds a free port (8080–8089) |
| Tunnel failed | Local network still works. Restart. |
| Git push failed | Check GitHub login + repo URL |
| Admin panel locked | Password is `radha` |
| Render service sleeps | Open the admin panel tab (polling keeps it warm) or upgrade plan |
| QR shows old URL | Admin panel → Refresh QR, or re-set `PERMANENT_DOMAIN` env var |

---

## 📜 License

MIT — free to use, modify, and share.  Vaibhav.
