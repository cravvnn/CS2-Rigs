"""Game maps: build each one once into a library .blend, then link or append it."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import bpy

from .core import log as log_mod
from .core import maplib, proc
from .core.catalog import GameMap
from .core.converter import failure_message
from .map_build import MAP_NAME_PROP
from .session import Session

BUILD_SCRIPT = Path(__file__).with_name("map_build.py")
BUILD_TIMEOUT = 3600
STALE_EXPORT_SECONDS = 7200
SKY_WORLD = "CS2 Rigs Sky"
SKY_COLOUR = (0.45, 0.62, 0.85)
SKY_STRENGTH = 0.3  # fill light only; the map's own sun does the rest


def library_path(session: Session, game_map: GameMap) -> Path:
    return maplib.library_file(session.cache.map_dir(game_map.name), game_map.name)


def newer_build_possible(session: Session, game_map: GameMap) -> bool:
    """True when the built map came from other game files, so a rebuild is worth offering.

    Add Map keeps using the old build; this only lets the panel offer a new one.
    """
    return maplib.newer_build_possible(
        session.cache.map_dir(game_map.name), Path(game_map.package), session.cache.game_version
    )


def _old_library(session: Session, name: str) -> Path | None:
    """The library of a map built by 0.2.x inside a version folder, if it is still there."""
    folders = [session.cache.version_dir]
    if session.cache.cache_dir.is_dir():
        folders += sorted(
            (p for p in session.cache.cache_dir.iterdir() if p.is_dir() and p != session.cache.version_dir),
            key=lambda p: p.name,
            reverse=True,
        )
    for folder in folders:
        candidate = folder / "maps" / name / f"{name}.blend"
        if candidate.is_file():
            return candidate
    return None


def _migrated_stamp(session: Session, game_map: GameMap, source: Path) -> dict:
    """What to record about a map that 0.2.x built in a version folder."""
    if source.name == session.cache.version_dir.name:
        # Built against the same game version, so the package's current details are what it saw.
        return maplib.stamp_for(Path(game_map.package), session.cache.game_version)
    return maplib.stamp_for(Path(game_map.package), source.name, stats=False)


def prepare_library(session: Session, game_map: GameMap) -> Path:
    """The map's library, moving one built by 0.2.x out of its version folder first.

    Returns the stable path when the old build was moved or nothing was built
    yet, and the old path when it could not be moved (linking it still works).
    """
    library = library_path(session, game_map)
    if library.is_file():
        return library
    old = _old_library(session, game_map.name)
    if old is None:
        return library
    if maplib.move_map(old.parent, library.parent):
        try:
            maplib.write_stamp(library.parent, _migrated_stamp(session, game_map, old.parent.parent.parent))
        except OSError:
            log_mod.logger.warning("could not write the build stamp for %s", game_map.name)
        return library
    return old


def _remove_old_exports(folder: Path) -> None:
    """Delete converted files and partial builds left by a run that was killed."""
    for old in list(folder.glob("export*")) + list(folder.glob("*.new.blend*")):
        try:
            old_age = time.time() - old.stat().st_mtime
        except OSError:
            continue
        if old_age < STALE_EXPORT_SECONDS:
            continue
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)


def build_library(session: Session, game_map: GameMap, blender: str, force: bool = False) -> Path:
    """Convert a map and build its library file. Slow, and safe to run off the main thread.

    A rebuild is made beside the library and moved into its place only when it
    is complete, so a linked scene keeps its path and never sees half a build.
    """
    library = prepare_library(session, game_map)
    if library.is_file() and not force:
        return library
    folder = library_path(session, game_map).parent
    folder.mkdir(parents=True, exist_ok=True)
    _remove_old_exports(folder)
    export_dir = folder / f"export-{os.getpid()}"
    shutil.rmtree(export_dir, ignore_errors=True)
    partial = folder / f"{game_map.name}.{os.getpid()}.new.blend"
    partial.unlink(missing_ok=True)
    try:
        glb = session.converter.export(Path(game_map.package), game_map.world_path, export_dir)
    except Exception:
        shutil.rmtree(export_dir, ignore_errors=True)
        raise
    command = [blender, "--background", "--factory-startup", "--python", str(BUILD_SCRIPT), "--", str(glb), str(partial), game_map.name]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=proc.creation_flags(),
        )
    except OSError as exc:
        shutil.rmtree(export_dir, ignore_errors=True)
        raise RuntimeError(f"Building {game_map.name} did not finish: {exc}") from exc
    proc.track(process)
    try:
        output, _ = process.communicate(timeout=BUILD_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        proc.kill_tree(process)
        process.communicate()
        raise RuntimeError(f"Building {game_map.name} did not finish: {exc}") from exc
    finally:
        proc.discard(process)
    if process.returncode != 0 or not partial.is_file():
        shutil.rmtree(export_dir, ignore_errors=True)
        partial.unlink(missing_ok=True)
        raise RuntimeError(failure_message(f"Building {game_map.name} failed", output))
    try:
        partial.replace(library)
    except OSError as exc:
        shutil.rmtree(export_dir, ignore_errors=True)
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not store {game_map.name} in {library}: {exc}") from exc
    # The library packs its textures, so the converted files are no longer needed.
    shutil.rmtree(export_dir, ignore_errors=True)
    try:
        maplib.write_stamp(library.parent, maplib.stamp_for(Path(game_map.package), session.cache.game_version))
    except OSError:
        log_mod.logger.warning("could not write the build stamp for %s", game_map.name)
    return library


class BuildJob:
    """One map build running on a worker thread. Nothing in it touches Blender data."""

    def __init__(self, session: Session, game_map: GameMap, force: bool = False):
        self.game_map = game_map
        self.force = force
        self.started = time.monotonic()
        self.library: Path | None = None
        self.error = ""
        self.cancelled = False
        self._tracker = proc.Tracker()
        self._thread = threading.Thread(target=self._run, args=(session, bpy.app.binary_path), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop now: the converter and background Blender are killed, and partial files are removed."""
        self.cancelled = True
        self._tracker.cancel()

    def _run(self, session: Session, blender: str) -> None:
        try:
            with proc.tracked(self._tracker):
                library = build_library(session, self.game_map, blender, force=self.force)
            if not self.cancelled:
                self.library = library
        except Exception as exc:  # reported on the main thread
            if not self.cancelled:
                self.error = str(exc)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def elapsed(self) -> str:
        seconds = int(time.monotonic() - self.started)
        return f"{seconds // 60}:{seconds % 60:02d}"


def maps_in_scene(scene: bpy.types.Scene) -> dict[str, bpy.types.Collection]:
    return {c[MAP_NAME_PROP]: c for c in scene.collection.children_recursive if MAP_NAME_PROP in c.keys()}


def missing_maps() -> dict[str, bpy.types.Library]:
    """Linked maps whose built file is gone: cleared, or from an older game version."""
    found = {}
    for library in bpy.data.libraries:
        path = Path(bpy.path.abspath(library.filepath))
        if path.parent.parent.name == "maps" and path.stem == path.parent.name and not path.is_file():
            found[path.stem] = library
    return found


def relink_linked_maps(map_root: Path) -> int:
    """Point maps linked from an old version folder at their stable file, when it exists."""
    root = Path(map_root)
    relinked = 0
    for library in list(bpy.data.libraries):
        path = Path(bpy.path.abspath(library.filepath))
        if path.parent.parent.name != "maps" or path.stem != path.parent.name:
            continue
        target = root / path.parent.name / path.name
        if target == path or not target.is_file():
            continue
        library.filepath = str(target)
        try:
            library.reload()
        except RuntimeError:
            library.filepath = str(path)  # leave a file that still points where it did
            continue
        relinked += 1
    return relinked


def remove_missing_map(name: str) -> None:
    """Drop a missing map's library, and the empty stand-ins Blender keeps for what it linked."""
    library = missing_maps().get(name)
    if library is not None:
        bpy.data.libraries.remove(library)


def add_map(context: bpy.types.Context, library: Path, name: str, link: bool) -> bpy.types.Collection | None:
    """Bring a built map into the scene, linked to its library file or as a local copy.

    A map already linked from a file that is gone is pointed at the new file instead,
    keeping its place in the scene; that returns None.
    """
    missing = missing_maps().get(name)
    if missing is not None:
        if link:
            missing.filepath = str(library)
            missing.reload()
            if name in maps_in_scene(context.scene):
                return None
        bpy.data.libraries.remove(missing)
    with bpy.data.libraries.load(str(library), link=link) as (source, target):
        if name not in source.collections:
            raise RuntimeError(f"{library.name} does not hold the map '{name}'. Rebuild it.")
        target.collections = [name]
    collection = target.collections[0]
    context.scene.collection.children.link(collection)
    return collection


def remove_map(scene: bpy.types.Scene, name: str) -> None:
    top = maps_in_scene(scene).get(name)
    if top is None:
        return
    collections = [top, *top.children_recursive]
    objects = list(top.all_objects)
    datas = {obj.data for obj in objects if obj.data is not None}
    bpy.data.batch_remove(objects)
    bpy.data.batch_remove(collections)
    # Materials and textures are left for Blender to drop when the file is saved and reopened.
    bpy.data.batch_remove([data for data in datas if data.users == 0])


def add_sky(scene: bpy.types.Scene) -> bool:
    """Give a scene without a world a plain daylight sky, since maps carry no baked lighting."""
    if scene.world is not None:
        return False
    world = bpy.data.worlds.get(SKY_WORLD) or bpy.data.worlds.new(SKY_WORLD)
    world.color = SKY_COLOUR
    # A world with nodes ignores its plain colour.
    for node in world.node_tree.nodes if world.node_tree else ():
        if node.type == "BACKGROUND":
            node.inputs["Color"].default_value = (*SKY_COLOUR, 1.0)
            node.inputs["Strength"].default_value = SKY_STRENGTH
    scene.world = world
    return True
