"""Tell the user when a newer CS2 Rigs is out, and install it only when they ask.

Blender's start checks GitHub once, on a worker thread, when online access is
allowed. The panel then shows "Update Available!" or "CS2-Rigs is Updated".
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import bpy

from .core import log as log_mod
from .core import update

# idle, offline, checking, available, current, failed, installed
status = "idle"
release: update.Release | None = None
message = ""
_thread: threading.Thread | None = None


def start_check(installed: str, asked: bool = False) -> bool:
    """Check GitHub in the background. False when Blender is set to work offline."""
    global status, _thread
    if installed_this_session():
        status = "installed"  # Blender still runs some of the old version until it restarts
        return True
    if not bpy.app.online_access or (bpy.app.background and not asked):
        status = "offline"
        return False
    if _thread is not None and _thread.is_alive():
        return True
    status = "checking"
    _thread = threading.Thread(target=_check, args=(installed,), name="cs2rig-update", daemon=True)
    _thread.start()
    if not bpy.app.background:
        bpy.app.timers.register(_redraw_when_done, first_interval=1.0)
    return True


def _check(installed: str) -> None:
    global status, release, message
    try:
        newest = update.fetch_newest()
    except update.UpdateError as exc:
        status, message = "failed", str(exc)
        log_mod.logger.info("%s", exc)
        return
    release = newest
    status = "available" if update.is_newer(newest, installed) else "current"
    log_mod.logger.info("newest release %s, installed %s", newest.version if newest else "none", installed)


def wait() -> None:
    if _thread is not None:
        _thread.join()


def _redraw_when_done():
    if _thread is not None and _thread.is_alive():
        return 1.0
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None


def repo_of(package: str) -> str:
    """The extension repository this add-on was installed into ('bl_ext.user_default.cs2_rigs')."""
    parts = package.split(".")
    return parts[1] if len(parts) == 3 and parts[0] == "bl_ext" else "user_default"


def download() -> Path:
    if release is None:
        raise update.UpdateError("No update has been found.")
    return update.download(release, Path(tempfile.mkdtemp(prefix="cs2rig-update-")))


def install_soon(package_file: Path, package: str, version: str) -> None:
    """Install once the button that asked for it has finished.

    Installing replaces and reloads this add-on; doing that from inside one of its
    own operators crashes Blender, so it waits for the next moment Blender is idle.
    """
    bpy.app.timers.register(lambda: install(package_file, package, version), first_interval=0.1)


def install(package_file: Path, package: str, version: str) -> None:
    """Hand the downloaded zip to Blender's extension installer, replacing this version."""
    global status, message
    try:
        result = bpy.ops.extensions.package_install_files(
            filepath=str(package_file), repo=repo_of(package), enable_on_install=True, overwrite=True
        )
    except RuntimeError as exc:
        result, message = set(), str(exc)
    if "FINISHED" not in result:
        status = "failed"
        log_mod.logger.error("installing CS2 Rigs %s failed: %s", version, message)
        return None
    # The add-on that is now loaded is the new one, with its own state; tell it what happened.
    bpy.app.driver_namespace[INSTALLED_KEY] = version
    status = "installed"
    log_mod.logger.info("installed CS2 Rigs %s", version)
    return None


INSTALLED_KEY = "cs2rig_update_installed"


def installed_this_session() -> str:
    """The version installed by an update earlier in this Blender session, or ''."""
    return bpy.app.driver_namespace.get(INSTALLED_KEY, "")
