"""Check every agent clip and draw its preview in a background Blender, then write clip_status.json.

The work runs on a thread that starts clip_build.py in background Blenders, so the
user's Blender stays usable. It resumes where an earlier run stopped.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import bpy

from .core import catalog as catalog_mod
from .core import clipdata, cliprun
from .session import Session

BUILD_SCRIPT = Path(__file__).with_name("clip_build.py")


def work_dir(session: Session) -> Path:
    return session.cache.version_dir / "clip_build"


def clips_to_build(session: Session, census: dict) -> list[str]:
    """Agent clips without a preview yet, gameplay clips first."""
    preview_dir = session.cache.clip_preview_dir()
    return [clip for clip in cliprun.agent_clips(census) if not (preview_dir / clipdata.preview_name(clip)).is_file()]


def write_status(session: Session, census: dict) -> dict:
    """clip_status.json from the census, this job's results and the file's earlier verdicts."""
    status_file = session.cache.clip_status_file()
    results = cliprun.read_results(work_dir(session) / "results.jsonl")
    status = clipdata.build_status(census, results, clipdata.load_status(status_file))
    clipdata.write_json(status_file, status)
    return status


class PreviewJob:
    """Builds clip previews on a worker thread. Nothing in it touches Blender data."""

    def __init__(self, session: Session, agent: catalog_mod.Agent):
        self.agent = agent
        self.started = time.monotonic()
        self.total = 0
        self._results: Path | None = None
        self._since = 0
        self.stage = "Reading clips"
        self.error = ""
        self._cancel = False
        self._thread = threading.Thread(target=self._run, args=(session, bpy.app.binary_path), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel = True

    def _run(self, session: Session, blender: str) -> None:
        try:
            self._build(session, blender)
        except Exception as exc:  # reported on the main thread
            self.error = str(exc)

    def _build(self, session: Session, blender: str) -> None:
        census = session.clip_census()
        rig = session.rig_export(self.agent)
        folder = work_dir(session)
        folder.mkdir(parents=True, exist_ok=True)
        census_file = session.cache.clip_census_file()
        results = folder / "results.jsonl"
        todo = clips_to_build(session, census)
        self._since = results.stat().st_size if results.is_file() else 0
        self._results, self.total, self.stage = results, len(todo), "Loading clips"
        job = {
            "cli": str(session.converter.cli), "package": str(session.install.main_package), "rig": str(rig),
            "census": str(census_file), "scratch": str(folder / "scratch"),
            "preview_dir": str(session.cache.clip_preview_dir()), "results": str(results), "previews": True,
        }
        for chunk in cliprun.chunks(todo):
            finished = cliprun.run_chunk(blender, BUILD_SCRIPT, {**job, "clips": chunk}, folder, log=lambda _: None,
                                         should_stop=lambda: self._cancel)
            write_status(session, census)
            if not finished:
                break
        shutil.rmtree(folder / "scratch", ignore_errors=True)
        write_status(session, census)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def done(self) -> int:
        return len(cliprun.read_results(self._results, self._since)) if self._results else 0

    @property
    def progress(self) -> str:
        """For the panel: what the job is doing, how far it is, and for how long."""
        seconds = int(time.monotonic() - self.started)
        count = f" {self.done}/{self.total}" if self.total else ""
        return f"{self.stage}{count}  {seconds // 60}:{seconds % 60:02d}"
