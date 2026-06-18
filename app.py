"""DropZone — bidirectional local WiFi file + clipboard transfer."""
import asyncio
import hashlib
import json
import re
import secrets
import socket
import time
import webbrowser
from pathlib import Path
from datetime import datetime
from typing import List

import uvicorn
import qrcode
import pyperclip
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Directories ───────────────────────────────────────────────────────────────
UPLOAD_DIR  = Path("./uploads")
OUTBOX_DIR  = Path("./outbox")
CONFIG_FILE = Path("./config.json")
for d in (UPLOAD_DIR, OUTBOX_DIR):
    d.mkdir(exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".heif"}
URL_RE     = re.compile(r'^https?://', re.I)
HISTORY_FILE = Path("./history.json")
MAX_HISTORY  = 200
AUTO_CLEAR_HOURS = 24

# Notification hooks — set by menubar.py / systray.py
on_file_received = None   # callable(name: str, size: str)
on_clip_received = None   # callable()

# ── Config / PIN ──────────────────────────────────────────────────────────────
def _load_or_create_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    pin = str(secrets.randbelow(10000)).zfill(4)
    cfg = {"pin": pin}
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return cfg

config = _load_or_create_config()
# Token is stable across restarts (derived from PIN)
VALID_TOKEN: str = hashlib.sha256(config["pin"].encode()).hexdigest()[:32]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SERVER_IP = get_local_ip()
PORT = 8765


# ── Auth ──────────────────────────────────────────────────────────────────────
def require_auth(request: Request):
    token = request.headers.get("X-Token") or request.query_params.get("token")
    if token != VALID_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── WebSocket manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self._connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def get_mdns_hostname():
    try:
        h = socket.gethostname()
        return h if h.endswith(".local") else f"{h}.local"
    except Exception:
        return None


def fmt_size(n: int) -> str:
    kb = n / 1024
    return f"{kb / 1024:.2f} MB" if kb >= 1024 else f"{kb:.1f} KB"


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


# ── Persistent history ────────────────────────────────────────────────────────

def history_add(direction: str, name: str, size: str, path: str = ""):
    try:
        entries = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        entries = []
    entries.insert(0, {
        "ts":        datetime.now().isoformat(timespec="seconds"),
        "direction": direction,
        "name":      name,
        "size":      size,
        "path":      path,
    })
    HISTORY_FILE.write_text(json.dumps(entries[:MAX_HISTORY], indent=2))


def history_get() -> list:
    try:
        return json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        return []


# ── Auto-clear old files ──────────────────────────────────────────────────────

async def _auto_clear():
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - AUTO_CLEAR_HOURS * 3600
        cleared = 0
        for folder in (UPLOAD_DIR, OUTBOX_DIR):
            for f in folder.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    cleared += 1
        if cleared:
            print(f"  [auto-clear]  {cleared} file(s) older than {AUTO_CLEAR_HOURS}h removed")


# ── Mobile UI ─────────────────────────────────────────────────────────────────
MOBILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>DropZone</title>
<link rel="manifest" href="/manifest.json">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="DropZone">
<meta name="theme-color" content="#f2f2f7">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f2f2f7; min-height: 100vh;
    color: #1c1c1e; -webkit-tap-highlight-color: transparent;
  }

  /* ── PIN screen ── */
  #pinScreen {
    position: fixed; inset: 0; z-index: 9999;
    background: #f2f2f7;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  #pinScreen.hidden { display: none; }
  .pin-card {
    background: #fff; border-radius: 24px; padding: 36px 28px;
    text-align: center; width: 100%; max-width: 320px;
    box-shadow: 0 4px 32px rgba(0,0,0,0.1);
  }
  .pin-icon   { font-size: 52px; margin-bottom: 14px; }
  .pin-card h2 { font-size: 22px; font-weight: 800; margin-bottom: 6px; }
  .pin-card p  { font-size: 14px; color: #8e8e93; margin-bottom: 24px; line-height: 1.4; }
  .pin-digits  { display: flex; gap: 10px; justify-content: center; margin-bottom: 12px; }
  .pin-digit {
    width: 58px; height: 68px; border: 2px solid #e5e5ea; border-radius: 14px;
    font-size: 26px; font-weight: 700; text-align: center; outline: none;
    background: #fafafa; color: #1c1c1e; transition: border-color 0.2s;
    caret-color: transparent;
  }
  .pin-digit:focus { border-color: #007aff; background: #fff; }
  .pin-error { color: #ff3b30; font-size: 14px; min-height: 22px; margin-bottom: 10px; }
  .pin-btn {
    width: 100%; padding: 15px; border: none; border-radius: 13px;
    font-size: 16px; font-weight: 700; background: #007aff; color: #fff; cursor: pointer;
    transition: opacity 0.15s;
  }
  .pin-btn:active { opacity: 0.75; }

  /* ── Incoming banner ── */
  #inbox { position: sticky; top: 0; z-index: 100; display: flex; flex-direction: column; }
  .inbox-item {
    background: #fff; border-bottom: 1px solid #f0f0f0;
    padding: 12px 16px; animation: slideDown 0.3s ease;
  }
  @keyframes slideDown {
    from { transform: translateY(-100%); opacity: 0; }
    to   { transform: translateY(0);     opacity: 1; }
  }
  .inbox-row {
    display: flex; align-items: center; gap: 12px;
  }
  .inbox-icon  { font-size: 28px; flex-shrink: 0; }
  .inbox-info  { flex: 1; min-width: 0; }
  .inbox-name  { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .inbox-meta  { font-size: 12px; color: #8e8e93; margin-top: 2px; }
  .inbox-preview {
    width: 100%; max-height: 200px; object-fit: cover;
    border-radius: 10px; margin-bottom: 10px; display: block;
  }
  .inbox-dl {
    flex-shrink: 0; background: #007aff; color: #fff;
    border: none; border-radius: 8px; padding: 8px 14px;
    font-size: 14px; font-weight: 600; cursor: pointer;
    text-decoration: none; display: inline-block;
  }
  .inbox-copy {
    flex-shrink: 0; background: #34c759; color: #fff;
    border: none; border-radius: 8px; padding: 8px 14px;
    font-size: 14px; font-weight: 600; cursor: pointer;
  }
  .inbox-dismiss {
    flex-shrink: 0; background: none; border: none;
    color: #aeaeb2; font-size: 22px; cursor: pointer; padding: 2px 6px; margin-left: 2px;
  }
  .inbox-clip-text {
    margin-top: 8px; padding: 10px 12px;
    background: #f2f2f7; border-radius: 10px;
    font-size: 14px; color: #3a3a3c; line-height: 1.4;
    word-break: break-word; max-height: 80px; overflow-y: auto;
  }

  /* ── Main content ── */
  .main-content { padding: 24px 16px 48px; }
  header { text-align: center; margin-bottom: 24px; }
  header h1 { font-size: 30px; font-weight: 800; letter-spacing: -0.5px; }
  header p  { font-size: 14px; color: #8e8e93; margin-top: 4px; }
  .ws-dot {
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: #ff3b30; margin-right: 6px; vertical-align: middle;
    transition: background 0.4s;
  }
  .ws-dot.live { background: #34c759; }

  .card {
    background: #fff; border-radius: 18px; padding: 20px; margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.07), 0 4px 16px rgba(0,0,0,0.04);
  }
  .card-label {
    font-size: 12px; font-weight: 700; letter-spacing: 0.6px;
    text-transform: uppercase; color: #8e8e93; margin-bottom: 14px;
  }

  .drop-zone {
    position: relative; border: 2px dashed #d1d1d6; border-radius: 14px;
    padding: 40px 20px; text-align: center; cursor: pointer;
    transition: border-color 0.2s, background 0.2s; overflow: hidden;
  }
  .drop-zone.drag-over { border-color: #007aff; background: #f0f6ff; }
  .drop-zone input[type=file] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .drop-icon    { font-size: 44px; line-height: 1; }
  .drop-primary { font-size: 16px; font-weight: 600; color: #1c1c1e; margin-top: 10px; }
  .drop-hint    { font-size: 13px; color: #aeaeb2; margin-top: 4px; }
  .selected-name {
    margin-top: 12px; font-size: 14px; font-weight: 500;
    color: #007aff; word-break: break-all; padding: 0 4px;
  }

  .progress-wrap { margin-top: 16px; display: none; }
  .progress-wrap.visible { display: block; }
  .progress-track { background: #e5e5ea; border-radius: 99px; height: 8px; overflow: hidden; }
  .progress-fill {
    background: linear-gradient(90deg, #007aff, #34aadc);
    height: 100%; width: 0%; border-radius: 99px; transition: width 0.1s ease;
  }
  .progress-text { font-size: 13px; color: #8e8e93; text-align: center; margin-top: 7px; }

  .btn {
    display: block; width: 100%; margin-top: 14px; padding: 16px;
    border: none; border-radius: 13px; font-size: 16px; font-weight: 700;
    cursor: pointer; transition: opacity 0.15s, transform 0.1s;
    background: #007aff; color: #fff;
  }
  .btn:active  { opacity: 0.75; transform: scale(0.98); }
  .btn:disabled { opacity: 0.35; pointer-events: none; }
  .btn.green   { background: #34c759; }

  textarea {
    width: 100%; border: 1.5px solid #e5e5ea; border-radius: 12px;
    padding: 14px; font-size: 15px; font-family: inherit;
    resize: vertical; min-height: 110px; outline: none;
    color: #1c1c1e; transition: border-color 0.2s; background: #fafafa;
  }
  textarea:focus { border-color: #007aff; background: #fff; }

  .toast {
    position: fixed; bottom: 36px; left: 50%;
    transform: translateX(-50%) translateY(12px);
    background: rgba(28,28,30,0.92); color: #fff;
    padding: 13px 26px; border-radius: 26px;
    font-size: 15px; font-weight: 600; white-space: nowrap;
    opacity: 0; pointer-events: none;
    transition: opacity 0.25s, transform 0.25s; z-index: 1000;
    backdrop-filter: blur(8px);
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>

<!-- PIN Screen -->
<div id="pinScreen">
  <div class="pin-card">
    <div class="pin-icon">🔐</div>
    <h2>DropZone</h2>
    <p>Enter the 4-digit PIN shown<br>in your laptop terminal</p>
    <div class="pin-digits">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p0">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p1">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p2">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p3">
    </div>
    <div class="pin-error" id="pinError"></div>
    <button class="pin-btn" id="pinSubmit">Unlock</button>
  </div>
</div>

<!-- Incoming banner -->
<div id="inbox"></div>

<!-- Main UI -->
<div class="main-content" id="mainContent" style="display:none">
  <header>
    <h1>⚡ DropZone</h1>
    <p><span class="ws-dot" id="wsDot"></span><span id="wsStatus">Connecting…</span></p>
  </header>

  <div class="card">
    <div class="card-label">Send File to Laptop</div>
    <div class="drop-zone" id="dropZone">
      <input type="file" id="fileInput" multiple>
      <div class="drop-icon">📤</div>
      <div class="drop-primary">Tap to choose files</div>
      <div class="drop-hint">or drag & drop</div>
    </div>
    <div class="selected-name" id="selectedName"></div>
    <div class="progress-wrap" id="progressWrap">
      <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
      <div class="progress-text" id="progressText">Uploading…</div>
    </div>
    <button class="btn" id="uploadBtn" disabled>Send File</button>
  </div>

  <div class="card">
    <div class="card-label">Clipboard → Laptop</div>
    <textarea id="textInput" placeholder="Type or paste — lands on laptop clipboard instantly"></textarea>
    <button class="btn" id="clipBtn">Send to Clipboard</button>
  </div>

  <div class="card">
    <div class="card-label">Recent Transfers</div>
    <div id="mobileHistory"><div style="color:#8e8e93;font-size:14px;text-align:center;padding:12px">Loading…</div></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);

// ── Auth ─────────────────────────────────────────────────────────────────────
const TOKEN_KEY = 'dz_token';
let authToken = localStorage.getItem(TOKEN_KEY);

function hdrs(extra = {}) {
  return { 'X-Token': authToken || '', ...extra };
}

async function checkAuth() {
  if (authToken) {
    try {
      const r = await fetch('/status', { headers: hdrs() });
      if (r.ok) { initApp(); return; }
    } catch {}
    localStorage.removeItem(TOKEN_KEY); authToken = null;
  }
  // Show PIN screen
}

// PIN digits auto-advance
const digits = [0,1,2,3].map(i => $('p' + i));
digits.forEach((inp, i) => {
  inp.addEventListener('input', () => {
    inp.value = inp.value.replace(/\D/g, '').slice(-1);
    if (inp.value && i < 3) digits[i+1].focus();
  });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !inp.value && i > 0) digits[i-1].focus();
    if (e.key === 'Enter') submitPin();
  });
});

async function submitPin() {
  const pin = digits.map(d => d.value).join('');
  if (pin.length < 4) { $('pinError').textContent = 'Enter all 4 digits'; return; }
  $('pinSubmit').disabled = true;
  try {
    const r = await fetch('/auth', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin}),
    });
    if (r.ok) {
      const d = await r.json();
      authToken = d.token;
      localStorage.setItem(TOKEN_KEY, authToken);
      $('pinScreen').classList.add('hidden');
      initApp();
    } else {
      $('pinError').textContent = 'Wrong PIN — check your terminal';
      digits.forEach(d => d.value = '');
      digits[0].focus();
    }
  } catch { $('pinError').textContent = 'Connection error'; }
  $('pinSubmit').disabled = false;
}
$('pinSubmit').addEventListener('click', submitPin);

// ── App init (after auth) ─────────────────────────────────────────────────────
function initApp() {
  $('pinScreen').classList.add('hidden');
  $('mainContent').style.display = '';
  connectWS();
  loadMobileHistory();
}

async function loadMobileHistory() {
  try {
    const r = await fetch('/history', { headers: hdrs() });
    if (!r.ok) return;
    const items = await r.json();
    const el = $('mobileHistory');
    if (!items.length) { el.innerHTML = '<div style="color:#8e8e93;font-size:14px;text-align:center;padding:12px">No transfers yet</div>'; return; }
    const dirLabel = d => ({
      mobile_to_laptop: '→ Laptop', laptop_to_mobile: '→ Phone',
      clipboard_to_laptop: '→ Laptop 📋', clipboard_to_mobile: '→ Phone 📋',
    }[d] || d);
    const dirColor = d => d.includes('laptop') ? '#007aff' : '#34c759';
    const dirIcon  = d => ({
      mobile_to_laptop:'📤', laptop_to_mobile:'📥',
      clipboard_to_laptop:'📋', clipboard_to_mobile:'📋',
    }[d] || '📁');
    el.innerHTML = items.slice(0, 15).map(h => `
      <div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #f2f2f7">
        <span style="font-size:22px;flex-shrink:0">${dirIcon(h.direction)}</span>
        <div style="flex:1;min-width:0">
          <div style="font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${h.name}</div>
          <div style="font-size:12px;color:#8e8e93">${h.size} · ${new Date(h.ts).toLocaleString()}</div>
        </div>
        <span style="font-size:11px;font-weight:700;color:${dirColor(h.direction)};flex-shrink:0">${dirLabel(h.direction)}</span>
      </div>
    `).join('');
  } catch {}
}

checkAuth();

// ── Toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, ms = 2800) {
  const t = $('toast');
  clearTimeout(toastTimer);
  t.textContent = msg;
  t.classList.add('show');
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

// ── Speed formatter ───────────────────────────────────────────────────────────
function fmtSpeed(bps) {
  if (bps >= 1048576) return (bps / 1048576).toFixed(1) + ' MB/s';
  if (bps >= 1024)    return (bps / 1024).toFixed(0) + ' KB/s';
  return Math.round(bps) + ' B/s';
}

// ── WebSocket (auto-reconnect) ────────────────────────────────────────────────
let ws, wsRetry = 1000;

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${authToken || ''}`);

  ws.onopen = () => {
    $('wsDot').classList.add('live');
    $('wsStatus').textContent = 'Connected — ready to receive';
    wsRetry = 1000;
  };
  ws.onclose = () => {
    $('wsDot').classList.remove('live');
    $('wsStatus').textContent = 'Reconnecting…';
    setTimeout(connectWS, Math.min(wsRetry *= 1.5, 15000));
  };
  ws.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'file')      showIncomingFile(msg);
    if (msg.type === 'clipboard') showIncomingClipboard(msg.text);
  };
}

// ── Incoming file banner ──────────────────────────────────────────────────────
function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    pdf:'📄', doc:'📝', docx:'📝', xls:'📊', xlsx:'📊', ppt:'📑', pptx:'📑',
    zip:'🗜️', rar:'🗜️', '7z':'🗜️',
    jpg:'🖼️', jpeg:'🖼️', png:'🖼️', gif:'🖼️', webp:'🖼️', heic:'🖼️',
    mp4:'🎬', mov:'🎬', mkv:'🎬', avi:'🎬',
    mp3:'🎵', wav:'🎵', m4a:'🎵',
  };
  return map[ext] || '📁';
}

function showIncomingFile({ name, size, url, is_image }) {
  const inbox = $('inbox');
  const item = document.createElement('div');
  item.className = 'inbox-item';

  const previewHtml = is_image
    ? `<img class="inbox-preview" src="${url}" alt="${name}" loading="lazy">`
    : '';

  item.innerHTML = `
    ${previewHtml}
    <div class="inbox-row">
      <span class="inbox-icon">${is_image ? '🖼️' : fileIcon(name)}</span>
      <div class="inbox-info">
        <div class="inbox-name">${name}</div>
        <div class="inbox-meta">${size} · from laptop</div>
      </div>
      <a class="inbox-dl" href="${url}" download="${name}">Download</a>
      <button class="inbox-dismiss">×</button>
    </div>
  `;
  item.querySelector('.inbox-dismiss').onclick = () => item.remove();
  inbox.prepend(item);
  if (navigator.vibrate) navigator.vibrate([60, 30, 60]);
}

function showIncomingClipboard(text) {
  const inbox = $('inbox');
  const item = document.createElement('div');
  item.className = 'inbox-item';
  const preview = text.length > 120 ? text.slice(0, 120) + '…' : text;
  item.innerHTML = `
    <div class="inbox-row">
      <span class="inbox-icon">📋</span>
      <div class="inbox-info">
        <div class="inbox-name">Clipboard from laptop</div>
        <div class="inbox-meta">${text.length} chars</div>
      </div>
      <button class="inbox-copy" id="cpBtn_${Date.now()}">Copy</button>
      <button class="inbox-dismiss">×</button>
    </div>
    <div class="inbox-clip-text">${preview.replace(/</g,'&lt;')}</div>
  `;
  item.querySelector('.inbox-copy').onclick = async function() {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
    }
    this.textContent = '✓'; this.style.background = '#636366';
    setTimeout(() => { this.textContent = 'Copy'; this.style.background = ''; }, 2000);
  };
  item.querySelector('.inbox-dismiss').onclick = () => item.remove();
  inbox.prepend(item);
  if (navigator.vibrate) navigator.vibrate(60);
}

// ── File upload (mobile → laptop) ────────────────────────────────────────────
let selectedFiles = null;

function setFiles(files) {
  selectedFiles = files;
  if (!files || !files.length) return;
  $('selectedName').textContent = files.length === 1
    ? files[0].name
    : `${files.length} files selected`;
  $('uploadBtn').disabled = false;
}

$('fileInput').addEventListener('change', () => setFiles($('fileInput').files));
$('dropZone').addEventListener('dragover',  e => { e.preventDefault(); $('dropZone').classList.add('drag-over'); });
$('dropZone').addEventListener('dragleave', ()  => $('dropZone').classList.remove('drag-over'));
$('dropZone').addEventListener('drop', e => {
  e.preventDefault(); $('dropZone').classList.remove('drag-over');
  setFiles(e.dataTransfer.files);
});

$('uploadBtn').addEventListener('click', async () => {
  if (!selectedFiles || !selectedFiles.length) return;
  $('uploadBtn').disabled = true;
  $('progressWrap').classList.add('visible');
  const total = selectedFiles.length;

  for (let i = 0; i < total; i++) {
    const fd = new FormData();
    fd.append('file', selectedFiles[i]);
    let lastT = Date.now(), lastL = 0;

    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');
      xhr.setRequestHeader('X-Token', authToken || '');

      xhr.upload.addEventListener('progress', e => {
        if (!e.lengthComputable) return;
        const now = Date.now(), dt = (now - lastT) / 1000;
        const speed = dt > 0.15 ? (e.loaded - lastL) / dt : 0;
        lastT = now; lastL = e.loaded;
        const pct = Math.round(e.loaded / e.total * 100);
        $('progressFill').style.width = pct + '%';
        const label = total > 1 ? `File ${i+1}/${total} — ${pct}%` : `${pct}%`;
        $('progressText').textContent = speed > 512 ? `${label}  ·  ${fmtSpeed(speed)}` : label;
      });

      xhr.addEventListener('load',  () => xhr.status === 200 ? resolve() : reject());
      xhr.addEventListener('error', reject);
      xhr.send(fd);
    });
  }

  $('progressFill').style.width = '100%';
  $('progressText').textContent = 'Done!';
  $('uploadBtn').classList.add('green');
  $('uploadBtn').textContent = '✓ Sent!';
  showToast('✅ ' + (total === 1 ? `"${selectedFiles[0].name}" saved` : `${total} files saved`) + ' to laptop');

  setTimeout(() => {
    $('progressWrap').classList.remove('visible');
    $('progressFill').style.width = '0%';
    $('selectedName').textContent = '';
    $('fileInput').value = '';
    selectedFiles = null;
    $('uploadBtn').disabled = true;
    $('uploadBtn').classList.remove('green');
    $('uploadBtn').textContent = 'Send File';
  }, 2200);
});

// ── Clipboard (mobile → laptop) ──────────────────────────────────────────────
$('clipBtn').addEventListener('click', async () => {
  const text = $('textInput').value.trim();
  if (!text) { showToast('⚠️  Nothing to send'); return; }
  $('clipBtn').disabled = true;
  try {
    const r = await fetch('/clipboard', {
      method: 'POST',
      headers: hdrs({'Content-Type': 'application/json'}),
      body: JSON.stringify({text}),
    });
    if (r.ok) { showToast('📋 Copied to laptop clipboard!'); $('textInput').value = ''; }
    else showToast('❌ Server error');
  } catch { showToast('❌ Connection lost'); }
  $('clipBtn').disabled = false;
});
</script>
</body>
</html>"""


# ── Laptop UI ─────────────────────────────────────────────────────────────────
LAPTOP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DropZone — Laptop</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1c1c1e; min-height: 100vh;
    display: flex; flex-direction: column; align-items: center;
    padding: 48px 24px 64px; color: #f2f2f7;
  }

  /* ── PIN screen ── */
  #pinScreen {
    position: fixed; inset: 0; z-index: 9999;
    background: #1c1c1e;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  #pinScreen.hidden { display: none; }
  .pin-card {
    background: #2c2c2e; border-radius: 24px; padding: 36px 28px;
    text-align: center; width: 100%; max-width: 320px;
    box-shadow: 0 4px 32px rgba(0,0,0,0.4);
  }
  .pin-icon   { font-size: 52px; margin-bottom: 14px; }
  .pin-card h2 { font-size: 22px; font-weight: 800; color: #f2f2f7; margin-bottom: 6px; }
  .pin-card p  { font-size: 14px; color: #8e8e93; margin-bottom: 24px; line-height: 1.4; }
  .pin-digits  { display: flex; gap: 10px; justify-content: center; margin-bottom: 12px; }
  .pin-digit {
    width: 58px; height: 68px; border: 2px solid #3a3a3c; border-radius: 14px;
    font-size: 26px; font-weight: 700; text-align: center; outline: none;
    background: #1c1c1e; color: #f2f2f7; transition: border-color 0.2s;
    caret-color: transparent;
  }
  .pin-digit:focus { border-color: #0a84ff; }
  .pin-error { color: #ff453a; font-size: 14px; min-height: 22px; margin-bottom: 10px; }
  .pin-btn {
    width: 100%; padding: 15px; border: none; border-radius: 13px;
    font-size: 16px; font-weight: 700; background: #0a84ff; color: #fff; cursor: pointer;
  }
  .pin-btn:hover { opacity: 0.88; }

  /* ── Main ── */
  #mainContent { width: 100%; max-width: 580px; display: none; flex-direction: column; align-items: center; }
  header { text-align: center; margin-bottom: 32px; width: 100%; }
  header h1 { font-size: 34px; font-weight: 800; letter-spacing: -1px; }
  header p  { font-size: 15px; color: #8e8e93; margin-top: 6px; }

  .status-bar {
    display: flex; align-items: center; gap: 8px;
    background: #2c2c2e; border-radius: 10px;
    padding: 10px 18px; margin-bottom: 28px;
    font-size: 14px; font-weight: 500; color: #aeaeb2; width: 100%;
  }
  .dot {
    width: 9px; height: 9px; border-radius: 50%;
    background: #ff3b30; transition: background 0.4s; flex-shrink: 0;
  }
  .dot.live { background: #34c759; animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

  .panel { width: 100%; }

  .card { background: #2c2c2e; border-radius: 18px; padding: 22px; margin-bottom: 16px; }
  .card-label {
    font-size: 12px; font-weight: 700; letter-spacing: 0.6px;
    text-transform: uppercase; color: #636366; margin-bottom: 14px;
  }

  /* Drop zone */
  .drop-zone {
    border: 2.5px dashed #3a3a3c; border-radius: 18px;
    padding: 52px 32px; text-align: center; cursor: pointer;
    transition: border-color 0.2s, background 0.2s; user-select: none;
  }
  .drop-zone.drag-over { border-color: #0a84ff; background: rgba(10,132,255,0.08); }
  .drop-zone.has-file  { border-color: #30d158; background: rgba(48,209,88,0.06); }
  .drop-zone input[type=file] { display: none; }
  .drop-icon  { font-size: 52px; line-height: 1; }
  .drop-title { font-size: 19px; font-weight: 700; color: #f2f2f7; margin-top: 12px; }
  .drop-hint  { font-size: 14px; color: #636366; margin-top: 6px; }

  .chip-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .file-chip {
    display: inline-flex; align-items: center; gap: 8px;
    background: #3a3a3c; border-radius: 10px; padding: 8px 14px;
    font-size: 13px; font-weight: 500; color: #f2f2f7; max-width: 260px; overflow: hidden;
  }
  .file-chip span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip-clear { background: none; border: none; color: #636366; font-size: 16px; cursor: pointer; padding: 0 2px; flex-shrink: 0; }
  .chip-clear:hover { color: #ff453a; }

  .prog-wrap { margin-top: 14px; display: none; }
  .prog-wrap.visible { display: block; }
  .prog-track { background: #3a3a3c; border-radius: 99px; height: 6px; overflow: hidden; }
  .prog-fill  {
    background: linear-gradient(90deg, #0a84ff, #32ade6);
    height: 100%; width: 0%; border-radius: 99px; transition: width 0.1s;
  }
  .prog-text  { font-size: 13px; color: #636366; text-align: center; margin-top: 8px; }

  .send-btn {
    width: 100%; margin-top: 16px; padding: 17px;
    border: none; border-radius: 13px; font-size: 16px; font-weight: 700;
    cursor: pointer; background: #0a84ff; color: #fff;
    transition: opacity 0.15s, transform 0.1s;
  }
  .send-btn:hover:not(:disabled) { opacity: 0.88; }
  .send-btn:active { transform: scale(0.98); }
  .send-btn:disabled { opacity: 0.3; pointer-events: none; }
  .send-btn.green { background: #30d158; }

  /* Clipboard push */
  textarea {
    width: 100%; border: 1.5px solid #3a3a3c; border-radius: 12px;
    padding: 14px; font-size: 15px; font-family: inherit;
    resize: vertical; min-height: 100px; outline: none;
    color: #f2f2f7; background: #1c1c1e;
    transition: border-color 0.2s;
  }
  textarea:focus { border-color: #0a84ff; }

  /* History */
  .history-empty { font-size: 14px; color: #48484a; text-align: center; padding: 16px 0; }
  .history-list  { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
  .history-item  {
    display: flex; align-items: center; gap: 12px;
    background: #3a3a3c; border-radius: 12px; padding: 12px 16px;
    animation: fadeIn 0.25s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; } }
  .h-icon { font-size: 22px; flex-shrink: 0; }
  .h-info { flex: 1; min-width: 0; }
  .h-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .h-meta { font-size: 12px; color: #636366; margin-top: 2px; }
  .h-badge {
    flex-shrink: 0; background: rgba(48,209,88,0.15); color: #30d158;
    border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: 600;
  }

  .toast {
    position: fixed; bottom: 32px; left: 50%;
    transform: translateX(-50%) translateY(10px);
    background: rgba(242,242,247,0.92); color: #1c1c1e;
    padding: 12px 24px; border-radius: 24px;
    font-size: 15px; font-weight: 600; white-space: nowrap;
    opacity: 0; pointer-events: none;
    transition: opacity 0.25s, transform 0.25s; z-index: 999;
    backdrop-filter: blur(12px);
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
</style>
</head>
<body>

<!-- PIN Screen -->
<div id="pinScreen">
  <div class="pin-card">
    <div class="pin-icon">🔐</div>
    <h2>DropZone</h2>
    <p>Enter your 4-digit PIN<br>shown in the terminal</p>
    <div class="pin-digits">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p0">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p1">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p2">
      <input class="pin-digit" type="tel" maxlength="1" inputmode="numeric" id="p3">
    </div>
    <div class="pin-error" id="pinError"></div>
    <button class="pin-btn" id="pinSubmit">Unlock</button>
  </div>
</div>

<!-- Main content -->
<div id="mainContent">
  <header>
    <h1>⚡ DropZone</h1>
    <p>Send files from laptop → mobile</p>
    <div style="margin-top:16px">
      <button onclick="toggleQR()" style="background:#2c2c2e;border:1px solid #3a3a3c;color:#f2f2f7;padding:8px 18px;border-radius:10px;font-size:14px;cursor:pointer;">📱 Show QR for Phone</button>
    </div>
    <div id="qrBox" style="display:none;margin-top:16px;background:#fff;padding:12px;border-radius:16px">
      <img src="/qr.png" width="200" height="200" style="display:block">
      <p style="color:#1c1c1e;font-size:12px;text-align:center;margin-top:6px">Scan with your phone camera</p>
    </div>
  </header>

  <div class="status-bar" style="width:100%;max-width:580px">
    <div class="dot" id="dot"></div>
    <span id="statusTxt">Waiting for mobile to connect…</span>
  </div>

  <div class="panel">

    <!-- File send card -->
    <div class="card">
      <div class="card-label">Send Files → Mobile</div>
      <div class="drop-zone" id="dropZone">
        <input type="file" id="fileInput" multiple>
        <div class="drop-icon">📡</div>
        <div class="drop-title" id="dropTitle">Drop files here</div>
        <div class="drop-hint">or click to browse · multiple files OK</div>
      </div>
      <div class="chip-list" id="chipList"></div>

      <div class="prog-wrap" id="progWrap">
        <div class="prog-track"><div class="prog-fill" id="progFill"></div></div>
        <div class="prog-text" id="progText">Sending…</div>
      </div>
      <button class="send-btn" id="sendBtn" disabled>Send to Mobile</button>
    </div>

    <!-- Clipboard push card -->
    <div class="card">
      <div class="card-label">Push Clipboard → Mobile</div>
      <textarea id="clipInput" placeholder="Type or paste text to send to mobile clipboard…"></textarea>
      <button class="send-btn" id="clipSendBtn">Push to Mobile</button>
    </div>

    <!-- History -->
    <div class="card">
      <div class="card-label">Sent this session</div>
      <div class="history-empty" id="historyEmpty">Nothing sent yet</div>
      <div class="history-list" id="historyList"></div>
    </div>

  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);

// ── Auth ─────────────────────────────────────────────────────────────────────
const TOKEN_KEY = 'dz_token';
let token = localStorage.getItem(TOKEN_KEY);

function hdrs(extra = {}) {
  return { 'X-Token': token || '', ...extra };
}

function toggleQR() {
  const box = document.getElementById('qrBox');
  box.style.display = box.style.display === 'none' ? 'inline-block' : 'none';
}

async function checkAuth() {
  if (token) {
    try {
      const r = await fetch('/status', { headers: hdrs() });
      if (r.ok) { initApp(); return; }
    } catch {}
    localStorage.removeItem(TOKEN_KEY); token = null;
  }
}

const digits = [0,1,2,3].map(i => $('p' + i));
digits.forEach((inp, i) => {
  inp.addEventListener('input', () => {
    inp.value = inp.value.replace(/\D/g, '').slice(-1);
    if (inp.value && i < 3) digits[i+1].focus();
  });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !inp.value && i > 0) digits[i-1].focus();
    if (e.key === 'Enter') submitPin();
  });
});

async function submitPin() {
  const pin = digits.map(d => d.value).join('');
  if (pin.length < 4) { $('pinError').textContent = 'Enter all 4 digits'; return; }
  $('pinSubmit').disabled = true;
  try {
    const r = await fetch('/auth', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pin}),
    });
    if (r.ok) {
      const d = await r.json();
      token = d.token;
      localStorage.setItem(TOKEN_KEY, token);
      $('pinScreen').classList.add('hidden');
      initApp();
    } else {
      $('pinError').textContent = 'Wrong PIN';
      digits.forEach(d => d.value = '');
      digits[0].focus();
    }
  } catch { $('pinError').textContent = 'Connection error'; }
  $('pinSubmit').disabled = false;
}
$('pinSubmit').addEventListener('click', submitPin);

function initApp() {
  $('pinScreen').classList.add('hidden');
  $('mainContent').style.display = 'flex';
  pollStatus();
  setInterval(pollStatus, 3000);
  loadLaptopHistory();
}

async function loadLaptopHistory() {
  try {
    const r = await fetch('/history', { headers: hdrs() });
    if (!r.ok) return;
    const items = await r.json();
    if (!items.length) return;
    $('historyEmpty').style.display = 'none';
    const dirIcon = d => ({
      mobile_to_laptop:'📤', laptop_to_mobile:'📥',
      clipboard_to_laptop:'📋', clipboard_to_mobile:'📋',
    }[d] || '📁');
    items.slice(0, 30).forEach(h => {
      const item = document.createElement('div');
      item.className = 'history-item';
      item.innerHTML = `
        <span class="h-icon">${dirIcon(h.direction)}</span>
        <div class="h-info">
          <div class="h-name">${h.name}</div>
          <div class="h-meta">${h.size} · ${new Date(h.ts).toLocaleString()}</div>
        </div>
        <span class="h-badge" style="${h.direction.includes('mobile_to') ? 'background:rgba(52,199,89,0.15);color:#30d158' : ''}">${h.direction.includes('to_laptop') || h.direction.includes('laptop') && !h.direction.includes('mobile') ? '← Received' : '→ Sent'}</span>
      `;
      $('historyList').appendChild(item);
    });
  } catch {}
}

checkAuth();

// ── Toast ────────────────────────────────────────────────────────────────────
let toastTimer;
function showToast(msg, ms = 2800) {
  clearTimeout(toastTimer);
  $('toast').textContent = msg;
  $('toast').classList.add('show');
  toastTimer = setTimeout(() => $('toast').classList.remove('show'), ms);
}

// ── Speed ────────────────────────────────────────────────────────────────────
function fmtSpeed(bps) {
  if (bps >= 1048576) return (bps / 1048576).toFixed(1) + ' MB/s';
  if (bps >= 1024)    return (bps / 1024).toFixed(0) + ' KB/s';
  return Math.round(bps) + ' B/s';
}
function fmtSize(b) {
  const kb = b / 1024;
  return kb >= 1024 ? (kb/1024).toFixed(2) + ' MB' : kb.toFixed(1) + ' KB';
}

// ── Status polling ────────────────────────────────────────────────────────────
let mobileCount = 0;
async function pollStatus() {
  try {
    const r = await fetch('/status', { headers: hdrs() });
    if (!r.ok) return;
    const d = await r.json();
    mobileCount = d.mobile_count;
    if (mobileCount > 0) {
      $('dot').classList.add('live');
      $('statusTxt').textContent = mobileCount === 1
        ? '1 mobile connected — ready to send'
        : `${mobileCount} mobiles connected`;
    } else {
      $('dot').classList.remove('live');
      $('statusTxt').textContent = 'Waiting for mobile to connect…';
    }
  } catch {}
}

// ── File icon ─────────────────────────────────────────────────────────────────
function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const map = {
    pdf:'📄', doc:'📝', docx:'📝', xls:'📊', xlsx:'📊', ppt:'📑', pptx:'📑',
    zip:'🗜️', rar:'🗜️', '7z':'🗜️',
    jpg:'🖼️', jpeg:'🖼️', png:'🖼️', gif:'🖼️', webp:'🖼️',
    mp4:'🎬', mov:'🎬', mkv:'🎬',
    mp3:'🎵', wav:'🎵', m4a:'🎵',
  };
  return map[ext] || '📁';
}

// ── File selection ─────────────────────────────────────────────────────────────
let selectedFiles = [];

function renderChips() {
  const list = $('chipList');
  list.innerHTML = '';
  selectedFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `<span>${f.name} (${fmtSize(f.size)})</span><button class="chip-clear">×</button>`;
    chip.querySelector('.chip-clear').onclick = () => {
      selectedFiles.splice(i, 1);
      renderChips();
      if (selectedFiles.length === 0) {
        $('dropZone').classList.remove('has-file');
        $('dropTitle').textContent = 'Drop files here';
        $('sendBtn').disabled = true;
      }
    };
    list.appendChild(chip);
  });
}

function setFiles(files) {
  selectedFiles = [...selectedFiles, ...Array.from(files)];
  $('dropZone').classList.add('has-file');
  $('dropTitle').textContent = `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} ready`;
  renderChips();
  $('sendBtn').disabled = false;
}

$('dropZone').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', () => { if ($('fileInput').files.length) setFiles($('fileInput').files); });
$('dropZone').addEventListener('dragover',  e => { e.preventDefault(); $('dropZone').classList.add('drag-over'); });
$('dropZone').addEventListener('dragleave', ()  => $('dropZone').classList.remove('drag-over'));
$('dropZone').addEventListener('drop', e => {
  e.preventDefault(); $('dropZone').classList.remove('drag-over');
  if (e.dataTransfer.files.length) setFiles(e.dataTransfer.files);
});

// ── Send files to mobile ──────────────────────────────────────────────────────
$('sendBtn').addEventListener('click', async () => {
  if (!selectedFiles.length) return;
  if (mobileCount === 0) { showToast('⚠️  No mobile connected yet'); return; }

  $('sendBtn').disabled = true;
  $('progWrap').classList.add('visible');
  const total = selectedFiles.length;

  for (let i = 0; i < total; i++) {
    const file = selectedFiles[i];
    const fd = new FormData();
    fd.append('file', file);
    let lastT = Date.now(), lastL = 0;

    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/send');
      xhr.setRequestHeader('X-Token', token || '');

      xhr.upload.addEventListener('progress', e => {
        if (!e.lengthComputable) return;
        const now = Date.now(), dt = (now - lastT) / 1000;
        const speed = dt > 0.15 ? (e.loaded - lastL) / dt : 0;
        lastT = now; lastL = e.loaded;
        const pct = Math.round(e.loaded / e.total * 100);
        $('progFill').style.width = pct + '%';
        const label = total > 1 ? `File ${i+1}/${total} — ${pct}%` : `${pct}%`;
        $('progText').textContent = speed > 512 ? `${label}  ·  ${fmtSpeed(speed)}` : label;
      });

      xhr.addEventListener('load',  () => xhr.status === 200 ? resolve() : reject());
      xhr.addEventListener('error', reject);
      xhr.send(fd);
    });

    // Add to history
    $('historyEmpty').style.display = 'none';
    const item = document.createElement('div');
    item.className = 'history-item';
    item.innerHTML = `
      <span class="h-icon">${fileIcon(file.name)}</span>
      <div class="h-info">
        <div class="h-name">${file.name}</div>
        <div class="h-meta">${fmtSize(file.size)} · ${new Date().toLocaleTimeString()}</div>
      </div>
      <span class="h-badge">Delivered</span>
    `;
    $('historyList').prepend(item);
  }

  $('progFill').style.width = '100%';
  $('progText').textContent = total > 1 ? `All ${total} files sent!` : 'Sent!';
  $('sendBtn').classList.add('green');
  $('sendBtn').textContent = `✓ Pushed ${total > 1 ? total + ' files' : '1 file'} to mobile!`;
  showToast(`📲 ${total > 1 ? total + ' files' : '"' + selectedFiles[0].name + '"'} sent to mobile`);

  setTimeout(() => {
    $('progWrap').classList.remove('visible');
    $('progFill').style.width = '0%';
    $('progText').textContent = 'Sending…';
    $('sendBtn').classList.remove('green');
    $('sendBtn').textContent = 'Send to Mobile';
    selectedFiles = [];
    $('chipList').innerHTML = '';
    $('fileInput').value = '';
    $('dropZone').classList.remove('has-file');
    $('dropTitle').textContent = 'Drop files here';
    $('sendBtn').disabled = true;
  }, 2200);
});

// ── Clipboard push (laptop → mobile) ────────────────────────────────────────
$('clipSendBtn').addEventListener('click', async () => {
  const text = $('clipInput').value.trim();
  if (!text) { showToast('⚠️  Nothing to send'); return; }
  if (mobileCount === 0) { showToast('⚠️  No mobile connected'); return; }
  $('clipSendBtn').disabled = true;
  try {
    const r = await fetch('/push-clipboard', {
      method: 'POST',
      headers: hdrs({'Content-Type': 'application/json'}),
      body: JSON.stringify({text}),
    });
    if (r.ok) { showToast('📋 Sent to mobile clipboard!'); $('clipInput').value = ''; }
    else showToast('❌ Failed');
  } catch { showToast('❌ Connection error'); }
  $('clipSendBtn').disabled = false;
});
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup():
    asyncio.create_task(_auto_clear())


@app.get("/", response_class=HTMLResponse)
async def mobile_ui():
    return MOBILE_HTML


@app.get("/laptop", response_class=HTMLResponse)
async def laptop_ui():
    return LAPTOP_HTML


@app.get("/manifest.json")
async def manifest():
    return JSONResponse({
        "name": "DropZone", "short_name": "DropZone",
        "description": "Local WiFi file transfer",
        "start_url": "/", "display": "standalone",
        "background_color": "#f2f2f7", "theme_color": "#007aff",
        "icons": [{
            "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='22' fill='%23007aff'/><text y='.9em' font-size='72' x='50%' dominant-baseline='middle' text-anchor='middle'>⚡</text></svg>",
            "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"
        }]
    })


@app.post("/auth")
async def auth(data: dict):
    if data.get("pin") == config["pin"]:
        return {"token": VALID_TOKEN}
    raise HTTPException(status_code=401, detail="Wrong PIN")


@app.get("/status")
async def status(_: None = Depends(require_auth)):
    return {"mobile_count": manager.count}


@app.get("/history")
async def get_history(_: None = Depends(require_auth)):
    return history_get()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if ws.query_params.get("token") != VALID_TOKEN:
        await ws.close(code=4001)
        return
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), _: None = Depends(require_auth)):
    safe_name = Path(file.filename).name or "unnamed"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = UPLOAD_DIR / f"{timestamp}_{safe_name}"
    content = await file.read()
    dest.write_bytes(content)
    size_str = fmt_size(len(content))
    print(f"  [mobile→laptop]  {safe_name}  ({size_str})  →  {dest}")
    history_add("mobile_to_laptop", safe_name, size_str, str(dest))
    if on_file_received:
        on_file_received(safe_name, size_str)
    return {"status": "ok", "path": str(dest)}


@app.post("/send")
async def send_to_mobile(file: UploadFile = File(...), _: None = Depends(require_auth)):
    safe_name = Path(file.filename).name or "unnamed"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stored_name = f"{timestamp}_{safe_name}"
    dest = OUTBOX_DIR / stored_name
    content = await file.read()
    dest.write_bytes(content)
    size_str = fmt_size(len(content))
    download_url = f"http://{SERVER_IP}:{PORT}/download/{stored_name}"
    await manager.broadcast({
        "type": "file", "name": safe_name,
        "size": size_str, "url": download_url,
        "is_image": is_image(safe_name),
    })
    print(f"  [laptop→mobile]  {safe_name}  ({size_str})  →  {manager.count} device(s)")
    history_add("laptop_to_mobile", safe_name, size_str, str(dest))
    return {"status": "ok", "notified": manager.count}


@app.post("/push-clipboard")
async def push_clipboard(data: dict, _: None = Depends(require_auth)):
    text = data.get("text", "")
    await manager.broadcast({"type": "clipboard", "text": text})
    preview = text[:72] + ("…" if len(text) > 72 else "")
    print(f"  [clip→mobile]  {preview!r}")
    history_add("clipboard_to_mobile", preview, f"{len(text)} chars")
    return {"status": "ok", "notified": manager.count}


@app.get("/qr", response_class=HTMLResponse)
async def qr_page():
    url = f"http://{SERVER_IP}:{PORT}"
    pin = config["pin"]
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>DropZone QR</title>
<style>
  body {{ margin:0; background:#1c1c1e; display:flex; flex-direction:column;
         align-items:center; justify-content:center; min-height:100vh;
         font-family:-apple-system,sans-serif; color:#f2f2f7; }}
  img  {{ border-radius:20px; background:#fff; padding:16px; width:260px; height:260px; }}
  h2   {{ margin:24px 0 6px; font-size:22px; font-weight:800; }}
  p    {{ color:#8e8e93; font-size:15px; margin:0; }}
  .pin {{ margin-top:16px; background:#2c2c2e; border-radius:14px;
          padding:12px 28px; font-size:28px; font-weight:800; letter-spacing:8px; }}
</style></head>
<body>
  <img src="/qr.png" alt="QR Code">
  <h2>⚡ DropZone</h2>
  <p>Scan with your phone camera to connect</p>
  <div class="pin">{pin}</div>
  <p style="margin-top:10px">Enter this PIN on your phone</p>
</body></html>"""


@app.get("/qr.png")
async def qr_image():
    import io
    from fastapi.responses import Response
    url = f"http://{SERVER_IP}:{PORT}"
    qr = qrcode.make(url)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.get("/download/{filename}")
async def download_file(filename: str):
    path = OUTBOX_DIR / Path(filename).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=path.name)


@app.post("/clipboard")
async def set_clipboard(data: dict, _: None = Depends(require_auth)):
    text = data.get("text", "")
    try:
        pyperclip.copy(text)
        preview = text[:72] + ("…" if len(text) > 72 else "")
        print(f"  [clip→laptop]  {preview!r}")
        history_add("clipboard_to_laptop", preview, f"{len(text)} chars")
        if URL_RE.match(text):
            webbrowser.open(text.strip())
            print(f"  [url-open]  {text.strip()}")
        if on_clip_received:
            on_clip_received()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ── Startup ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    SERVER_IP = get_local_ip()
    mobile_url = f"http://{SERVER_IP}:{PORT}"
    laptop_url = f"http://localhost:{PORT}/laptop"

    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║          ⚡  DropZone  ⚡                 ║")
    print("  ║     Bidirectional Local File Transfer     ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  ┌──────────────────────────────────────────┐")
    print(f"  │   🔐  PIN:  {config['pin']}                            │")
    print("  │   Enter this on phone + laptop browser   │")
    print("  └──────────────────────────────────────────┘")
    print()
    print("  ── Mobile (scan once, bookmark forever) ───")
    print_qr(mobile_url)
    print(f"  Phone URL:   {mobile_url}")
    print()
    print("  ── Laptop ──────────────────────────────────")
    print(f"  Laptop URL:  {laptop_url}")
    print()
    print("  uploads/ ← mobile→laptop    outbox/ ← laptop→mobile")
    print("  Tip: python3 install.py  to auto-start on boot")
    print("  Press Ctrl+C to stop.")
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
