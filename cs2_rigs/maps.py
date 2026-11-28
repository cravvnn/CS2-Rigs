"""Game maps: build each one once into a library .blend, then link or append it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import bpy

from .core import log as log_mod
from .core import maplib, maplights, proc
from .core.catalog import GameMap
from .core.converter import failure_message
from .map_build import (
    MAP_NAME_PROP,
    PROBE_INDEX_PROP,
    PROBE_WORLDS_PROP,
    SKY_WORLD_PROP,
    WORLD_KIND_PROP,
)
from .session import Session

BUILD_SCRIPT = Path(__file__).with_name("map_build.py")
BUILD_TIMEOUT = 3600
STALE_EXPORT_SECONDS = 7200
SKY_WORLD = "CS2 Rigs Sky"
SKY_COLOUR = (0.45, 0.62, 0.85)
SKY_STRENGTH = 0.3  # fill light only; the map's own sun does the rest
SKY_WORLD_PREFIX = "CS2 Sky "
LIGHTS_SUFFIX = "_lights"
EXTRAS_NAME = "map_extras.json"


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


def _write_extras(session: Session, game_map: GameMap, export_dir: Path) -> Path | None:
    """Read the map's sky and lights next to the geometry, for map_build.py.

    A map without an entity lump, or whose sky cannot be exported, still builds:
    it just keeps the glTF sun and the fallback sky.
    """
    try:
        extras = maplights.extract(
            session.converter,
            Path(game_map.package),
            Path(session.install.main_package),
            game_map.name,
            export_dir / "sky",
            export_dir / "probes",
        )
    except Exception as exc:  # the geometry is still worth building
        log_mod.logger.warning("could not read the sky and lights of %s: %s", game_map.name, exc)
        return None
    path = export_dir / EXTRAS_NAME
    try:
        path.write_text(json.dumps(extras.as_dict(), separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        log_mod.logger.warning("could not store the sky and lights of %s: %s", game_map.name, exc)
        return None
    return path


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
    extras_path = _write_extras(session, game_map, export_dir)
    command = [blender, "--background", "--factory-startup", "--python", str(BUILD_SCRIPT), "--", str(glb), str(partial), game_map.name]
    if extras_path is not None:
        command.append(str(extras_path))
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


def reload_map(name: str) -> bool:
    """Reload a linked map after its file was rebuilt. False when the scene holds a copy instead."""
    for library in bpy.data.libraries:
        path = Path(bpy.path.abspath(library.filepath))
        if path.parent.parent.name != "maps" or path.stem != name:
            continue
        try:
            library.reload()
        except RuntimeError:
            return False
        return True
    return False


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
            # Linked in another scene only: keep that one, and link the same map here.
            collection = next((c for c in bpy.data.collections if c.library == missing and c.name == name), None)
            if collection is not None:
                context.scene.collection.children.link(collection)
                return collection
        else:
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
    map_world = scene.world if scene.world is not None and scene.world.get(MAP_NAME_PROP) == name else None
    if map_world is not None:
        scene.world = None
    collections = [top, *top.children_recursive]
    objects = list(top.all_objects)
    datas = {obj.data for obj in objects if obj.data is not None}
    bpy.data.batch_remove(objects)
    bpy.data.batch_remove(collections)
    # Materials and textures are left for Blender to drop when the file is saved and reopened.
    bpy.data.batch_remove([data for data in datas if data.users == 0])
    if map_world is not None and map_world.users == 0:
        bpy.data.worlds.remove(map_world)
    # Copied maps own their probe worlds; linked ones live in the map's library.
    for world in list(bpy.data.worlds):
        if world.get(MAP_NAME_PROP) == name and world.library is None and (
            world.users == 0 or (world.users == 1 and world.use_fake_user)
        ):
            bpy.data.worlds.remove(world)


def sky_world_name(name: str) -> str:
    return f"{SKY_WORLD_PREFIX}{name}"


def lights_collection(collection: bpy.types.Collection) -> bpy.types.Collection | None:
    return next((child for child in collection.children if child.name.endswith(LIGHTS_SUFFIX)), None)


def show_map_lights(collection: bpy.types.Collection, on: bool) -> bool:
    """Show or hide a map's lights, for a map that has them. False when it has none."""
    lights = lights_collection(collection)
    if lights is None:
        return False
    lights.hide_viewport = not on
    lights.hide_render = not on
    return True


def _map_world(name: str, library: Path | None, collection: bpy.types.Collection, link: bool) -> bpy.types.World | None:
    """The map's sky World from its library, linked or copied to match the map."""
    stored = collection.get(SKY_WORLD_PROP)
    expected = str(stored) if stored else sky_world_name(name)
    source_file = Path(bpy.path.abspath(collection.library.filepath)) if collection.library is not None else None
    for world in bpy.data.worlds:
        if world.get(MAP_NAME_PROP) != name:
            continue
        if world.get(WORLD_KIND_PROP, "sky") != "sky":
            continue
        if source_file is not None:
            if world.library is not None and Path(bpy.path.abspath(world.library.filepath)) == source_file:
                return world
        elif not link and world.library is None:
            return world
    if library is None:
        return None
    library = Path(bpy.path.abspath(str(library)))
    if not library.is_file():
        return None
    try:
        with bpy.data.libraries.load(str(library), link=link) as (source, target):
            if expected not in source.worlds:
                return None
            target.worlds = [expected]
    except (OSError, RuntimeError):
        return None
    return target.worlds[0] if target.worlds else None


def probe_worlds(collection: bpy.types.Collection) -> list[dict]:
    """The indoor cubemap worlds a built map carries, best first."""
    raw = collection.get(PROBE_WORLDS_PROP)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("world")]


def _stored_world(
    name: str,
    kind: str,
    index: int,
    world_name: str,
    library: Path | None,
    link: bool,
) -> bpy.types.World | None:
    """A map's sky or probe World from its library, linked or copied to match the map."""
    source_file = Path(bpy.path.abspath(str(library))) if library is not None else None
    for world in bpy.data.worlds:
        if world.get(MAP_NAME_PROP) != name or world.get(WORLD_KIND_PROP) != kind:
            continue
        if kind == "probe" and int(world.get(PROBE_INDEX_PROP, -1)) != index:
            continue
        if not link:
            if world.library is None:
                return world
        elif world.library is not None and Path(bpy.path.abspath(world.library.filepath)) == source_file:
            return world
    if library is None:
        return None
    library = Path(bpy.path.abspath(str(library)))
    if not library.is_file():
        return None
    try:
        with bpy.data.libraries.load(str(library), link=link) as (source, target):
            if world_name not in source.worlds:
                return None
            target.worlds = [world_name]
    except (OSError, RuntimeError):
        return None
    return target.worlds[0] if target.worlds else None


def apply_map_probes(
    scene: bpy.types.Scene,
    name: str,
    library: Path | None,
    collection: bpy.types.Collection,
    index: int | None = None,
) -> int | None:
    """Light the scene with one of this map's indoor cubemap HDRI probes.

    Returns the probe's array index, or None when the map was built without
    probes. `index` picks a probe; without it the most indoor one is used.
    """
    probes = probe_worlds(collection)
    if not probes:
        return None
    fallback = probes[0] if index is None else None
    chosen = next((probe for probe in probes if int(probe.get("index", -1)) == index), fallback)
    if chosen is None:
        return None
    link = collection.library is not None
    source = Path(bpy.path.abspath(collection.library.filepath)) if collection.library is not None else library
    world = _stored_world(name, "probe", int(chosen["index"]), str(chosen["world"]), source, link)
    if world is None:
        return None
    scene.world = world
    show_map_lights(collection, True)
    return int(chosen["index"])


def _plain_world() -> bpy.types.World:
    world = bpy.data.worlds.get(SKY_WORLD) or bpy.data.worlds.new(SKY_WORLD)
    world.color = SKY_COLOUR
    # A world with nodes ignores its plain colour.
    for node in world.node_tree.nodes if world.node_tree else ():
        if node.type == "BACKGROUND":
            node.inputs["Color"].default_value = (*SKY_COLOUR, 1.0)
            node.inputs["Strength"].default_value = SKY_STRENGTH
    return world


def _is_factory_default_world(world: bpy.types.World) -> bool:
    """True for Blender's untouched startup 'World' (plain grey Background, no images or links)."""
    if world.library is not None or world.name != "World" or world.get(MAP_NAME_PROP):
        return False
    tree = world.node_tree
    if tree is None:
        return True
    if any(n.type not in ("BACKGROUND", "OUTPUT_WORLD") for n in tree.nodes):
        return False
    bg = next((n for n in tree.nodes if n.type == "BACKGROUND"), None)
    if bg is None or bg.inputs["Color"].is_linked or bg.inputs["Strength"].is_linked:
        return False
    col = bg.inputs["Color"].default_value
    return abs(col[0] - 0.050876) < 0.01 and abs(col[1] - 0.050876) < 0.01 and abs(col[2] - 0.050876) < 0.01


def add_sky(
    scene: bpy.types.Scene,
    name: str = "",
    library: Path | None = None,
    collection: bpy.types.Collection | None = None,
    link: bool = True,
    use_map: bool = True,
) -> bool:
    """Give a scene without a custom world a sky, using the map's own when it has one.

    `use_map` also shows the map's lights; with it off the old plain sky is used
    and the lights stay hidden, which keeps a linked map's geometry cheap.
    """
    if collection is not None:
        show_map_lights(collection, use_map)
    if scene.world is not None and not _is_factory_default_world(scene.world) and scene.world.name != SKY_WORLD:
        return False
    world = None
    if use_map and collection is not None and name:
        world = _map_world(name, library, collection, link)
    if world is None:
        world = _plain_world()
    scene.world = world
    return True


def apply_map_sky(
    scene: bpy.types.Scene,
    name: str,
    library: Path | None,
    collection: bpy.types.Collection,
) -> bool:
    """Put this map's real sky in the scene and show its lights again."""
    world = _map_world(name, library, collection, collection.library is not None)
    if world is None:
        return False
    scene.world = world
    show_map_lights(collection, True)
    return True
