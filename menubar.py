"""
DropZone — macOS Menu Bar App
Run: python3 menubar.py
Auto-start: python3 install.py
"""
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import rumps
except ImportError:
    print("Run:  pip3 install rumps")
    sys.exit(1)

import uvicorn
import app as dz

UPLOADS = (Path(__file__).parent / "uploads").resolve()
OUTBOX  = (Path(__file__).parent / "outbox").resolve()


class DropZoneApp(rumps.App):
    def __init__(self):
        super(DropZoneApp, self).__init__("⚡", quit_button=None)

        self._ip         = dz.get_local_ip()
        self._mobile_url = f"http://{self._ip}:{dz.PORT}"
        self._laptop_url = f"http://localhost:{dz.PORT}/laptop"

        # Dynamic items stored as references
        self._status_item = rumps.MenuItem("○  No mobiles connected")
        self._status_item.set_callback(None)

        self._info_item = rumps.MenuItem(f"🌐  {self._mobile_url}")
        self._info_item.set_callback(None)

        self.menu = [
            self._status_item,
            None,
            rumps.MenuItem("💻  Open Laptop UI",            callback=self.open_laptop),
            rumps.MenuItem(f"🔐  Copy PIN  ({dz.config['pin']})", callback=self.copy_pin),
            None,
            rumps.MenuItem("📥  Open Uploads Folder",       callback=self.open_uploads),
            rumps.MenuItem("📤  Open Outbox Folder",        callback=self.open_outbox),
            None,
            self._info_item,
            None,
            rumps.MenuItem("⏹  Quit DropZone",             callback=self.quit_app),
        ]

        # Wire notification hooks into app.py
        dz.on_file_received = self._notify_file
        dz.on_clip_received = self._notify_clip

        self._start_server()
        self._print_qr()

        rumps.notification(
            "⚡ DropZone running",
            "Ready to transfer files",
            f"Open on phone: {self._mobile_url}",
        )

    # ── QR ───────────────────────────────────────────────────────────────────

    def _print_qr(self):
        url = self._mobile_url
        pin = dz.config['pin']
        width = max(len(url), 40)
        bar = "─" * (width + 4)
        print(f"\n  ╔{bar}╗")
        print(f"  ║   ⚡  DropZone is running{' ' * (width - 22)}  ║")
        print(f"  ╠{bar}╣")
        print(f"  ║   📱  Phone URL :  {url}{' ' * (width - len(url) - 16)}  ║")
        print(f"  ║   💻  Laptop UI :  {self._laptop_url}{' ' * (width - len(self._laptop_url) - 16)}  ║")
        print(f"  ║   🔐  PIN       :  {pin}{' ' * (width - len(pin) - 16)}  ║")
        print(f"  ╚{bar}╝\n")

    # ── Server ────────────────────────────────────────────────────────────────

    def _start_server(self):
        threading.Thread(
            target=uvicorn.run,
            kwargs={"app": dz.app, "host": "0.0.0.0", "port": dz.PORT, "log_level": "warning"},
            daemon=True,
        ).start()

    # ── Tick: update title + status every 3 s ────────────────────────────────

    @rumps.timer(3)
    def _tick(self, _):
        count = dz.manager.count
        if count == 0:
            self.title = "⚡"
            self._status_item.title = "○  No mobiles connected"
        else:
            label = "mobile" if count == 1 else "mobiles"
            self.title = f"⚡ {count}"
            self._status_item.title = f"●  {count} {label} connected"

    # ── Notifications ─────────────────────────────────────────────────────────

    def _notify_file(self, name: str, size: str):
        rumps.notification(
            "DropZone  📥  File received",
            name,
            f"{size}  ·  saved to uploads/",
        )

    def _notify_clip(self):
        rumps.notification(
            "DropZone  📋  Clipboard updated",
            "Text from mobile",
            "Now in your laptop clipboard",
        )

    # ── Menu actions ──────────────────────────────────────────────────────────

    def open_laptop(self, _):
        webbrowser.open(self._laptop_url)

    def copy_pin(self, _):
        import pyperclip
        pyperclip.copy(dz.config["pin"])
        rumps.notification("DropZone  🔐", "PIN copied to clipboard", f"PIN: {dz.config['pin']}")

    def open_uploads(self, _):
        UPLOADS.mkdir(exist_ok=True)
        subprocess.Popen(["open", str(UPLOADS)])

    def open_outbox(self, _):
        OUTBOX.mkdir(exist_ok=True)
        subprocess.Popen(["open", str(OUTBOX)])

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    DropZoneApp().run()
