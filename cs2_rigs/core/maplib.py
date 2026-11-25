"""Built map libraries, where they live and what they were built from. No Blender imports here.

A built map is kept outside the game version folders, so a game update does not
strand it. A small stamp next to the library records the game version and the
map package's size and modification time, which is enough to tell whether a
newer build would use newer game files.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import proc

STAMP_NAME = "build.json"
STAMP_VERSION = 1


def library_file(map_dir: Path, name: str) -> Path:
    return Path(map_dir) / f"{name}.blend"


def stamp_for(package: Path, game: str, stats: bool = True) -> dict:
    """What a build made now would record: the game it read and the map package it converted."""
    stamp = {"version": STAMP_VERSION, "game": game}
    if stats:
        try:
            stat = Path(package).stat()
        except OSError:
            pass
        else:
            stamp["size"] = stat.st_size
            stamp["mtime"] = int(stat.st_mtime)
    return stamp


def read_stamp(map_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(map_dir) / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_stamp(map_dir: Path, stamp: dict) -> None:
    """Write the stamp through a per-process temporary file, so it is never half-read."""
    map_dir = Path(map_dir)
    map_dir.mkdir(parents=True, exist_ok=True)
    path = map_dir / STAMP_NAME
    partial = proc.temp_name(path)
    partial.write_text(json.dumps(stamp, separators=(",", ":")), encoding="utf-8")
    proc.replace_file(partial, path)


def newer_build_possible(map_dir: Path, package: Path, game: str) -> bool:
    """Whether the built map came from other game files, so rebuilding would use newer ones.

    The old build stays usable; this only says a rebuild is worth offering.
    """
    map_dir = Path(map_dir)
    if not library_file(map_dir, map_dir.name).is_file():
        return False
    return read_stamp(map_dir) != stamp_for(package, game)


def move_map(source: Path, target: Path) -> bool:
    """Move a map built inside a version folder to its stable folder.

    Files already in the target are kept; only missing ones are moved in. Returns
    True when the source can be deleted, False when some of it could not be moved.
    """
    source, target = Path(source), Path(target)
    if not source.is_dir():
        return True  # another process moved it first
    try:
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            destination = target / item.name
            if destination.exists():
                # The target's copy is kept; a second one is not needed.
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            else:
                shutil.move(str(item), str(destination))
        source.rmdir()
    except OSError:
        return False
    return True
