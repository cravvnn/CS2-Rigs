"""Game maps: build each one once into a library .blend, then link or append it."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import bpy

from .core.catalog import GameMap
from .map_build import MAP_NAME_PROP
from .session import Session

BUILD_SCRIPT = Path(__file__).with_name("map_build.py")
BUILD_TIMEOUT = 3600
SKY_WORLD = "CS2 Rigs Sky"
SKY_COLOUR = (0.45, 0.62, 0.85)
SKY_STRENGTH = 0.3  # fill light only; the map's own sun does the rest


def library_path(session: Session, game_map: GameMap) -> Path:
    return session.cache.map_dir(game_map.name) / f"{game_map.name}.blend"


def build_library(session: Session, game_map: GameMap, blender: str) -> Path:
    """Convert a map and build its library file. Slow, and safe to run off the main thread."""
    library = library_path(session, game_map)
    if library.is_file():
        return library
    export_dir = library.parent / "export"
    glb = session.converter.export(Path(game_map.package), game_map.world_path, export_dir)
    command = [blender, "--background", "--factory-startup", "--python", str(BUILD_SCRIPT), "--", str(glb), str(library), game_map.name]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=BUILD_TIMEOUT, creationflags=flags
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Building {game_map.name} did not finish: {exc}") from exc
    if result.returncode != 0 or not library.is_file():
        tail = (result.stdout + result.stderr).strip().splitlines()[-8:]
        raise RuntimeError(f"Building {game_map.name} failed:\n" + "\n".join(tail))
    # The library packs its textures, so the converted files are no longer needed.
    shutil.rmtree(export_dir, ignore_errors=True)
    return library


class BuildJob:
    """One map build running on a worker thread. Nothing in it touches Blender data."""

    def __init__(self, session: Session, game_map: GameMap):
        self.game_map = game_map
        self.started = time.monotonic()
        self.library: Path | None = None
        self.error = ""
        self._thread = threading.Thread(target=self._run, args=(session, bpy.app.binary_path), daemon=True)
        self._thread.start()

    def _run(self, session: Session, blender: str) -> None:
        try:
            self.library = build_library(session, self.game_map, blender)
        except Exception as exc:  # reported on the main thread
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


def add_map(context: bpy.types.Context, library: Path, name: str, link: bool) -> bpy.types.Collection:
    """Bring a built map into the scene, linked to its library file or as a local copy."""
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