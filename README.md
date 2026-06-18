# ⚡ DropZone

### Phone ↔ Laptop file transfer over local WiFi — in under 3 seconds.

No cables. No cloud. No app install. Just open a URL on your phone.

> Built by [Rajat Sankhyan](https://github.com/rajatsankhyan)

---

## The problem

You're on your laptop. A file is on your phone. Your options are:

- **AirDrop** — only works Apple → Apple
- **Bluetooth** — slow and painful
- **WhatsApp yourself** — seriously?
- **USB cable** — where even is it
- **Google Drive / iCloud** — uploads to the internet just to come back down

There had to be a better way.

---

## DropZone

Run one command on your laptop. Scan a QR code with your phone. Done — you now have a two-way file bridge over your local WiFi that's faster than anything else.

**Works on:** Mac, Windows, Linux laptops + any phone with a browser (iPhone, Android, anything).

---

## Demo

```
$ python3 menubar.py

  ╔══════════════════════════════════════════╗
  ║          ⚡  DropZone  ⚡                 ║
  ║     Bidirectional Local File Transfer     ║
  ╚══════════════════════════════════════════╝

  🔐  PIN:  9238

  ── Mobile UI (scan once, bookmark forever) ─

  █▀▀▀▀▀▀▀█ ▀███▀▀█ █▀█ ▀▄▀ █
  █ █▀▀▀█ █▄██▄█  ▄ ▀▀▀ ▀▄ ██
  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀

  Phone URL:   http://10.0.0.5:8765
  Laptop URL:  http://localhost:8765/laptop
```

Scan → bookmark → never open a terminal again.

---

## Features

| | |
|---|---|
| 📤 **Phone → Laptop** | Tap, pick any file, it saves to `~/dropzone/uploads/` |
| 📥 **Laptop → Phone** | Drag file to laptop UI → mobile gets a notification + download link |
| 📋 **Clipboard sync** | Send text/URLs in both directions instantly |
| 🔗 **URL auto-open** | Send a URL from your phone → laptop opens it in the browser |
| 🖼️ **Image preview** | Photos show as thumbnails on mobile before downloading |
| 🔐 **PIN protected** | 4-digit PIN blocks anyone else on the WiFi |
| ⚡ **Transfer speed** | Live MB/s display — local WiFi = fast |
| 📜 **History log** | Every transfer saved to `history.json` |
| 🧹 **Auto-cleanup** | Files older than 24h deleted automatically |
| 🍎 **Mac menu bar** | Lives in your menu bar — click to access, shows connected devices |
| 🪟 **Windows tray** | Same experience in the Windows system tray |
| 🚀 **Auto-start** | One-time setup — starts automatically on every login |

---

## Install

**Requirements:** Python 3.9+

```bash
git clone https://github.com/rajatsankhyan/dropzone
cd dropzone
pip3 install -r requirements.txt
```

**First run:**
```bash
python3 menubar.py        # Mac — runs as a menu bar app
python3 systray.py        # Windows — runs in system tray
python3 app.py            # Any OS — terminal mode
```

**Auto-start on boot (run once):**
```bash
python3 install.py        # Mac: LaunchAgent  |  Windows: Startup folder
```

**Build a distributable .app + .dmg (Mac):**
```bash
python3 build.py
# Output: dist/DropZone.dmg
```

---

## How it works

1. **Laptop** runs a local FastAPI server on port 8765
2. **Phone** opens the URL in any browser (bookmark it after the first scan)
3. Files transfer over your local WiFi — **nothing touches the internet**
4. Laptop saves received files to `./uploads/`
5. Outbox files (laptop→phone) are served from `./outbox/` and auto-deleted after 24h

---

## Security

- PIN protected — 4-digit code required on every new device
- LAN only — the server binds to `0.0.0.0` but only reachable on your local network
- No accounts, no tracking, no telemetry
- All code is in 3 Python files — read it in 10 minutes

---

## File structure

```
dropzone/
├── app.py           ← FastAPI server + all logic
├── menubar.py       ← macOS menu bar app
├── systray.py       ← Windows system tray app
├── install.py       ← auto-start installer
├── build.py         ← PyInstaller .dmg packager
├── run.bat          ← Windows one-click launcher
├── requirements.txt
├── config.json      ← PIN (auto-generated)
├── history.json     ← transfer log
├── uploads/         ← files received from phone
└── outbox/          ← files sent to phone (auto-cleared)
```

---

## Why not just use X?

| Tool | Problem |
|---|---|
| AirDrop | Apple devices only |
| Snapdrop | Needs internet, files go through a server |
| LocalSend | Requires app install on phone |
| USB | Requires a cable, slow setup |
| Cloud (Drive, iCloud) | Files leave your network |
| **DropZone** | ✅ No app · ✅ No internet · ✅ Any device · ✅ Fast |

---

## License

MIT — free to use, modify, and distribute.

---

---

Built by [Rajat Sankhyan](https://github.com/rajatsankhyan) · MIT License

Built with FastAPI · uvicorn · rumps · qrcode · pyperclip
