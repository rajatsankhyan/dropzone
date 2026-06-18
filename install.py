"""
DropZone installer — sets up auto-start on boot.
Run once:  python3 install.py
Remove:    python3 install.py uninstall
"""

import os
import sys
import subprocess
from pathlib import Path

WORKDIR = Path(__file__).parent.resolve()
PYTHON  = sys.executable
LABEL   = "com.dropzone.app"

def _pick_script() -> Path:
    """Use menubar.py on Mac and systray.py on Windows if available."""
    if sys.platform == "darwin":
        mb = WORKDIR / "menubar.py"
        try:
            import rumps  # noqa: F401
            if mb.exists():
                return mb
        except ImportError:
            pass
    elif sys.platform == "win32":
        st = WORKDIR / "systray.py"
        try:
            import pystray  # noqa: F401
            if st.exists():
                return st
        except ImportError:
            pass
    return WORKDIR / "app.py"

SCRIPT = _pick_script()


# ── Mac ───────────────────────────────────────────────────────────────────────

def install_mac():
    log_path  = Path.home() / "Library" / "Logs" / "dropzone.log"
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LABEL}.plist"

    plist_path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{PYTHON}</string>
    <string>{SCRIPT}</string>
  </array>
  <key>WorkingDirectory</key>  <string>{WORKDIR}</string>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string>{log_path}</string>
  <key>StandardErrorPath</key> <string>{log_path}</string>
</dict>
</plist>
""")

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load",   str(plist_path)], capture_output=True)

    if result.returncode == 0:
        print("✅  DropZone installed — will start automatically on every login.")
        print(f"    Log:   {log_path}")
        print(f"    Plist: {plist_path}")
        print()
        print("    DropZone is now running in the background.")
        print("    To uninstall:  python3 install.py uninstall")
    else:
        print("❌  launchctl load failed:")
        print(result.stderr.decode())


def uninstall_mac():
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if not plist_path.exists():
        print("DropZone is not installed as a startup item.")
        return
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink()
    print("✅  DropZone removed from startup.")


# ── Windows ───────────────────────────────────────────────────────────────────

def install_windows():
    # pythonw runs Python without opening a console window
    pythonw = Path(PYTHON).with_name("pythonw.exe")
    runner  = str(pythonw) if pythonw.exists() else PYTHON

    startup = (
        Path(os.environ["APPDATA"])
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )
    bat_path = startup / "dropzone.bat"

    bat_path.write_text(
        f'@echo off\n'
        f'cd /d "{WORKDIR}"\n'
        f'start "" "{runner}" "{SCRIPT}"\n'
    )

    print("✅  DropZone installed — will start automatically on every login.")
    print(f"    Startup file: {bat_path}")
    print()
    print("    To start right now, double-click  run.bat")
    print("    To uninstall:  python install.py uninstall")


def uninstall_windows():
    startup  = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )
    bat_path = startup / "dropzone.bat"
    if not bat_path.exists():
        print("DropZone is not installed as a startup item.")
        return
    bat_path.unlink()
    print("✅  DropZone removed from startup.")


# ── Linux (systemd user service) ─────────────────────────────────────────────

def install_linux():
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "dropzone.service"

    service_path.write_text(f"""[Unit]
Description=DropZone local file transfer
After=network.target

[Service]
ExecStart={PYTHON} {SCRIPT}
WorkingDirectory={WORKDIR}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")

    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "dropzone"])
    print("✅  DropZone installed as a systemd user service.")
    print("    To uninstall:  python3 install.py uninstall")


def uninstall_linux():
    subprocess.run(["systemctl", "--user", "disable", "--now", "dropzone"], capture_output=True)
    service_path = Path.home() / ".config" / "systemd" / "user" / "dropzone.service"
    if service_path.exists():
        service_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    print("✅  DropZone removed from startup.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    action = sys.argv[1].lower() if len(sys.argv) > 1 else "install"

    if sys.platform == "darwin":
        uninstall_mac() if action == "uninstall" else install_mac()

    elif sys.platform == "win32":
        uninstall_windows() if action == "uninstall" else install_windows()

    elif sys.platform.startswith("linux"):
        uninstall_linux() if action == "uninstall" else install_linux()

    else:
        print(f"Platform '{sys.platform}' not yet supported for auto-start.")
        print(f"Manually add this to your startup:  python3 {SCRIPT}")


if __name__ == "__main__":
    main()
