"""Child processes started by the add-on, and how to stop all of them. No Blender imports here.

A long job runs a converter (and for maps and clips also a background Blender)
on a worker thread. Killing the direct child alone leaves the processes it
started alive, so on Windows `taskkill /T` takes the whole tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

_CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_LOCAL = threading.local()  # the tracker of the thread's current job, if any
_ALL: set[subprocess.Popen] = set()
_ALL_LOCK = threading.Lock()


def kill_tree(process: subprocess.Popen) -> None:
    """Stop a process and everything it started."""
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                creationflags=_CREATE_NO_WINDOW,
            )
        else:
            process.kill()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


class Tracker:
    """Every child one job starts, so cancelling the job stops all of them."""

    def __init__(self):
        self.cancelled = False
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def add(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.add(process)
            cancelled = self.cancelled
        if cancelled:
            kill_tree(process)

    def discard(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def cancel(self) -> None:
        """Stop every child now. Safe to call from any thread, and more than once."""
        self.cancelled = True
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            kill_tree(process)


@contextmanager
def tracked(tracker: Tracker):
    """Make `track()` inside this block add to `tracker`."""
    previous = getattr(_LOCAL, "tracker", None)
    _LOCAL.tracker = tracker
    try:
        yield
    finally:
        _LOCAL.tracker = previous


def track(process: subprocess.Popen) -> None:
    """Register a child with the current thread's job and the process-wide list."""
    with _ALL_LOCK:
        _ALL.add(process)
    tracker = getattr(_LOCAL, "tracker", None)
    if tracker is not None:
        tracker.add(process)


def discard(process: subprocess.Popen) -> None:
    with _ALL_LOCK:
        _ALL.discard(process)
    tracker = getattr(_LOCAL, "tracker", None)
    if tracker is not None:
        tracker.discard(process)


def kill_all() -> None:
    """Stop every child this Blender started, whether or not it belonged to a job."""
    with _ALL_LOCK:
        processes = list(_ALL)
    for process in processes:
        kill_tree(process)


def creation_flags() -> int:
    """Windows flags that keep a console window from opening for a child."""
    return _CREATE_NO_WINDOW


def staging_name(prefix: str) -> str:
    """A short folder name that two processes and two threads cannot share."""
    pid = os.getpid() & 0xFFFFF
    thread = threading.get_ident() & 0xFFFF
    return f".{prefix}{pid:05x}{thread:04x}"


def temp_name(path: Path) -> Path:
    """A temporary name beside `path` that another Blender's copy cannot share."""
    return path.with_name(f"{path.name}.{os.getpid()}.part")


def replace_file(source: Path, target: Path) -> None:
    """Move a complete temporary file into place.

    Two Blenders can finish the same cache file at once; Windows then sometimes
    refuses the second replace even though the target is already a valid file.
    """
    try:
        os.replace(source, target)
    except PermissionError:
        if not target.is_file():
            raise
        Path(source).unlink(missing_ok=True)
