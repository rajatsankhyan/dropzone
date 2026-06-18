"""
DropZone — Windows System Tray App
Run: python systray.py
Auto-start: python install.py
"""
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Run:  pip install pystray pillow")
    sys.exit(1)

import uvicorn
import app as dz

UPLOADS = (Path(__file__).parent / "uploads").resolve()
OUTBOX  = (Path(__file__).parent / "outbox").resolve()


def _make_icon(label: str = "⚡") -> Image.Image:
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 122, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), "DZ", fill="white", anchor="mm", font=font)
    return img


def _toast(title: str, msg: str):
    """Windows balloon notification via pystray."""
    if _tray:
        _tray.notify(msg, title)


_tray: pystray.Icon | None = None


def _build_menu() -> pystray.Menu:
    ip  = dz.get_local_ip()
    mob = f"http://{ip}:{dz.PORT}"
    lap = f"http://localhost:{dz.PORT}/laptop"
    pin = dz.config["pin"]

    def open_mobile(_):    webbrowser.open(mob)
    def open_laptop(_):    webbrowser.open(lap)
    def open_uploads(_):   subprocess.Popen(["explorer", str(UPLOADS)])
    def open_outbox(_):    subprocess.Popen(["explorer", str(OUTBOX)])
    def copy_pin(_):
        import pyperclip
        pyperclip.copy(pin)
        _toast("PIN copied", f"Your PIN is {pin}")
    def quit_app(_):
        _tray.stop()

    return pystray.Menu(
        pystray.MenuItem(f"DropZone  —  PIN: {pin}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open on Phone",       open_mobile),
        pystray.MenuItem("Open Laptop UI",      open_laptop),
        pystray.MenuItem(f"Copy PIN ({pin})",   copy_pin),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Uploads Folder", open_uploads),
        pystray.MenuItem("Open Outbox Folder",  open_outbox),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit DropZone",       quit_app),
    )


def _start_server():
    threading.Thread(
        target=uvicorn.run,
        kwargs={"app": dz.app, "host": "0.0.0.0", "port": dz.PORT, "log_level": "warning"},
        daemon=True,
    ).start()


def main():
    global _tray

    dz.on_file_received = lambda name, size: _toast("📥 File received", f"{name}  ·  {size}")
    dz.on_clip_received = lambda: _toast("📋 Clipboard updated", "Text from mobile copied")

    _start_server()

    _tray = pystray.Icon(
        name="DropZone",
        icon=_make_icon(),
        title="DropZone ⚡",
        menu=_build_menu(),
    )

    # Show startup notification after icon is ready
    def _on_setup(icon):
        icon.visible = True
        _toast("⚡ DropZone running", f"Open http://{dz.get_local_ip()}:{dz.PORT} on your phone")

    _tray.run(_on_setup)


if __name__ == "__main__":
    main()
