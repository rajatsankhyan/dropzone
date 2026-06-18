"""
DropZone build script — creates a standalone macOS .app + .dmg
Run: python3 build.py

Requires: pip3 install pyinstaller
Output:   dist/DropZone.dmg
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT    = Path(__file__).parent.resolve()
DIST    = ROOT / "dist"
BUILD   = ROOT / "build"
APP     = DIST / "DropZone.app"
DMG     = DIST / "DropZone.dmg"


def run(cmd: list, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller…")
        run([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build_app():
    print("\n── Building .app ──────────────────────────────────")
    args = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--windowed",           # no terminal window
        "--name", "DropZone",
        "--osx-bundle-identifier", "com.dropzone.app",
        # Hidden uvicorn internals PyInstaller misses
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "anyio",
        "--hidden-import", "anyio._backends._asyncio",
        "--hidden-import", "multipart",
        "--hidden-import", "email.mime.text",
        "--collect-all", "uvicorn",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        str(ROOT / "menubar.py"),
    ]
    run(args, cwd=ROOT)
    print(f"  ✅  Built: {APP}")


def make_dmg():
    print("\n── Creating .dmg ───────────────────────────────────")
    if DMG.exists():
        DMG.unlink()

    # Use hdiutil (built into macOS — no extra tools needed)
    # First create a staging folder
    staging = DIST / "_dmg_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    shutil.copytree(APP, staging / "DropZone.app")

    # Symlink to /Applications so user can drag-install
    apps_link = staging / "Applications"
    apps_link.symlink_to("/Applications")

    run([
        "hdiutil", "create",
        "-volname", "DropZone",
        "-srcfolder", str(staging),
        "-ov", "-format", "UDZO",
        str(DMG),
    ])

    shutil.rmtree(staging)
    print(f"  ✅  Created: {DMG}")
    print(f"\n  Share {DMG.name} — users drag DropZone → Applications and run it.")


def main():
    if sys.platform != "darwin":
        print("❌  build.py currently supports macOS only.")
        print("    For Windows .exe, run:  pyinstaller --onefile --windowed systray.py")
        sys.exit(1)

    ensure_pyinstaller()
    build_app()
    make_dmg()

    size_mb = DMG.stat().st_size / 1_048_576
    print(f"\n  📦  {DMG.name}  ({size_mb:.1f} MB)  — ready to share!")


if __name__ == "__main__":
    main()
