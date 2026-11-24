"""Load every agent clip onto one agent in headless Blender, judge it, and write previews and clip_status.json.

    python tools/clip_sweep.py <work_dir> [--agent ctm_sas/ctm_sas] [--categories world,ui_anims]
                               [--chunk 200] [--hours 3] [--retry collapsed,timeout,...] [--only clip,clip]

Clips are checked in chunks of about 200 by cs2_rigs/clip_build.py, one Blender at
a time. A chunk whose Blender crashes or stalls for 20 minutes is restarted after
the clip it was on, which is recorded as failed. Results go to
<work_dir>/results.jsonl, append-only, so a stopped sweep picks up where it left
off; the last line for a clip wins. Conversions go to <work_dir>/scratch and are
deleted folder by folder. Previews and clip_status.json are written to the add-on
cache, where the clip browser reads them. Clips for other skeletons are never
loaded; the census alone marks them.
"""

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cs2_rigs"))
from clip_census import setup  # noqa: E402
from core import catalog, clipdata, cliprun  # noqa: E402

BLENDER = r"C:\Users\Andrew\AppData\Local\Programs\Blender Foundation\Blender 5.3\blender.exe"
BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "cs2_rigs" / "clip_build.py"
MIN_FREE = 40e9
SCRATCH_CAP = 3e9


def free_space_problem(work_dir: Path) -> str:
    """'' when there is room to carry on, else why not."""
    for drive in ("C:\\", "D:\\"):
        free = shutil.disk_usage(drive).free
        if free < MIN_FREE:
            return f"only {free / 1e9:.1f} GB free on {drive}"
    used = sum(f.stat().st_size for f in work_dir.rglob("*") if f.is_file())
    if used > SCRATCH_CAP:
        return f"the work folder holds {used / 1e9:.1f} GB"
    return ""


def write_status(census: dict, results_path: Path, cache) -> dict:
    status = clipdata.build_status(census, cliprun.read_results(results_path))
    clipdata.write_json(cache.clip_status_file(), status)
    counts: dict[str, int] = {}
    for entry in status["clips"].values():
        key = f"{entry['category']:24} {entry['verdict']}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("--agent", default="ctm_sas/ctm_sas")
    parser.add_argument("--categories", default="world,ui_anims", help="top folders to check, in order; 'all' for every agent clip")
    parser.add_argument("--chunk", type=int, default=cliprun.CHUNK)
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--retry", default="", help="verdicts to check again, comma separated")
    parser.add_argument("--only", default="", help="check just these clips (comma separated full paths)")
    parser.add_argument("--no-previews", action="store_true")
    parser.add_argument("--blender", default=BLENDER)
    args = parser.parse_args()

    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    results_path = work_dir / "results.jsonl"
    log_path = work_dir / "sweep.log"

    def log(message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as out:
            out.write(line + "\n")

    install, conv, cache = setup()
    data = catalog.load_or_build_catalog(conv, install.main_package, cache.catalog_file)
    census = clipdata.load_or_build_census(conv, install.main_package, data["clips"], cache.clip_census_file())
    shutil.copyfile(cache.clip_census_file(), work_dir / "census.json")
    agent = next(a for a in catalog.catalog_agents(data) if a.key == args.agent)
    rig = conv.export_model(install.main_package, agent.model_path, cache.clip_dir(agent.key, "rig"), [catalog.BASE_CLIP], materials=False)

    candidates = cliprun.agent_clips(census)
    if args.only:
        todo = [c for c in args.only.split(",") if c in candidates]
    else:
        tops = args.categories.split(",")
        if tops != ["all"]:
            candidates = [c for c in candidates if c.removeprefix(clipdata.CLIP_PREFIX).split("/", 1)[0] in tops]
            candidates.sort(key=lambda c: (tops.index(c.removeprefix(clipdata.CLIP_PREFIX).split("/", 1)[0]), c))
        done = cliprun.read_results(results_path)
        retry = set(filter(None, args.retry.split(",")))
        todo = [c for c in candidates if c not in done or done[c]["verdict"] in retry]
    log(f"sweep: {len(todo)} clips to check on {agent.key}, rig {rig}")

    job = {
        "cli": str(conv.cli), "package": str(install.main_package), "rig": str(rig),
        "census": str(work_dir / "census.json"), "scratch": str(work_dir / "scratch"),
        "preview_dir": str(cache.clip_preview_dir()), "results": str(results_path), "previews": not args.no_previews,
    }
    deadline = time.monotonic() + args.hours * 3600
    for number, chunk in enumerate(cliprun.chunks(todo, args.chunk), 1):
        problem = free_space_problem(work_dir)
        if problem:
            log(f"stopping: {problem}")
            break
        if time.monotonic() > deadline:
            log("stopping: time box reached")
            break
        log(f"chunk {number}: {len(chunk)} clips from {clipdata.folder_of(chunk[0])}")
        cliprun.run_chunk(args.blender, BUILD_SCRIPT, {**job, "clips": chunk}, work_dir, log)
        write_status(census, results_path, cache)
    shutil.rmtree(work_dir / "scratch", ignore_errors=True)
    for key, count in sorted(write_status(census, results_path, cache).items()):
        log(f"  {key}: {count}")


if __name__ == "__main__":
    main()
