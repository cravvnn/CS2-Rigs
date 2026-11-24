"""Run clip_build.py in background Blenders over many clips, surviving crashes and stalls. No Blender imports here.

Used by the add-on's Build Clip Previews job and by tools/clip_sweep.py. Results
are one JSON line per clip in an append-only file; the last line for a clip wins,
so a stopped run picks up where it left off.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import clipdata

STALL = 20 * 60  # seconds without a finished clip before Blender is restarted
POLL = 2.0
CHUNK = 200


def read_results(path: Path, since: int = 0) -> dict[str, dict]:
    """Results by clip, from the whole file or from byte offset `since` on."""
    results: dict[str, dict] = {}
    try:
        with open(path, "rb") as handle:
            handle.seek(since)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return results
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a line cut short by a crash
        if isinstance(record, dict) and "clip" in record:
            results[record["clip"]] = record
    return results


def append_result(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


def chunks(clips: list[str], size: int = CHUNK) -> list[list[str]]:
    """Whole folders per chunk, so each folder is converted once."""
    folders: dict[str, list[str]] = {}
    for clip in clips:
        folders.setdefault(clipdata.folder_of(clip), []).append(clip)
    result: list[list[str]] = []
    current: list[str] = []
    for members in folders.values():
        if current and len(current) + len(members) > size:
            result.append(current)
            current = []
        current = current + members
    if current:
        result.append(current)
    return result


def agent_clips(census: dict) -> list[str]:
    """Clips made for the agent skeleton, gameplay ones first."""
    order = ("world", "ui_anims")
    clips = [c for c, info in census["clips"].items() if info.get("skeleton") == clipdata.WORLDMODEL and not info.get("missing")]

    def rank(clip):
        top = clip.removeprefix(clipdata.CLIP_PREFIX).split("/", 1)[0]
        return (order.index(top) if top in order else len(order), clip)

    return sorted(clips, key=rank)


def run_chunk(
    blender: str,
    script: Path,
    job: dict,
    work_dir: Path,
    log: Callable[[str], None] = print,
    stall: float = STALL,
    should_stop: Callable[[], bool] = lambda: False,
) -> bool:
    """Run Blender over job['clips'] until each has a result, restarting after a crash or stall.

    A clip Blender was on when it crashed or stalled is recorded as failed and skipped.
    Returns False when `should_stop` asked to stop.
    """
    results_path = Path(job["results"])
    since = results_path.stat().st_size if results_path.is_file() else 0
    pending = list(job["clips"])
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    while pending:
        job_file = work_dir / "job.json"
        job_file.write_text(json.dumps({**job, "clips": pending}), encoding="utf-8")
        command = [blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script), "--", str(job_file)]
        started = last_change = time.monotonic()
        seen, stalled, stopped = len(pending), False, False
        with open(work_dir / "blender.log", "a", encoding="utf-8") as blender_log:
            process = subprocess.Popen(command, stdout=blender_log, stderr=subprocess.STDOUT, creationflags=flags)
            while process.poll() is None:
                time.sleep(POLL)
                left = [c for c in pending if c not in read_results(results_path, since)]
                if len(left) != seen:
                    seen, last_change = len(left), time.monotonic()
                if should_stop() or time.monotonic() - last_change > stall:
                    stopped, stalled = should_stop(), not should_stop()
                    process.kill()
                    process.wait()
        if stopped:
            return False
        left = [c for c in pending if c not in read_results(results_path, since)]
        log(f"  {len(pending) - len(left)} clips in {time.monotonic() - started:.0f} s, exit {process.returncode}")
        if left and (stalled or process.returncode != 0 or len(left) == len(pending)):
            culprit, left = left[0], left[1:]
            if stalled:
                verdict, note = "timeout", f"Loading stalled for {stall / 60:.0f} minutes."
            else:
                verdict, note = "converter-error", f"Blender stopped while loading this clip (exit {process.returncode})."
            append_result(results_path, {"clip": culprit, "verdict": verdict, "note": note, "metrics": {}})
            log(f"  {verdict}: {culprit}")
        pending = left
    return True
