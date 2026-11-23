"""Turn a converted map .glb into a library .blend. Runs inside a background Blender:

    blender --background --factory-startup --python map_build.py -- <map.glb> <out.blend> <name>

A map is millions of triangles and hundreds of textures, so it is built once into
its own file and scenes link to it instead of carrying a copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

MAP_NAME_PROP = "cs2rig_map"
# Collections inside a map, in display order.
GEOMETRY, OVERLAYS, LIGHTS = "Geometry", "Overlays", "Lights"
# The converter names decal-like meshes after the overlay they came from.
OVERLAY_MARKER = "_overlay"


def _group_for(obj: bpy.types.Object) -> str:
    if obj.type == "LIGHT":
        return LIGHTS
    if OVERLAY_MARKER in obj.name:
        return OVERLAYS
    return GEOMETRY


def build(glb: Path, out: Path, name: str) -> dict[str, int]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb), import_shading="NORMALS", disable_bone_shape=True)
    scene = bpy.context.scene
    objects = list(scene.objects)
    if not any(obj.type == "MESH" for obj in objects):
        raise RuntimeError(f"{glb.name} has no geometry.")

    top = bpy.data.collections.new(name)
    top[MAP_NAME_PROP] = name
    scene.collection.children.link(top)
    groups: dict[str, bpy.types.Collection] = {}
    counts: dict[str, int] = {}
    for obj in objects:
        group = _group_for(obj)
        if group not in groups:
            groups[group] = bpy.data.collections.new(f"{name} {group}")
            top.children.link(groups[group])
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        groups[group].objects.link(obj)
        counts[group] = counts.get(group, 0) + 1

    # Overlays are thin decals lying on other surfaces; their shadows only add noise.
    for obj in groups[OVERLAYS].objects if OVERLAYS in groups else ():
        obj.visible_shadow = False

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    # Textures stay as files next to the .glb; relative paths keep the folder movable.
    bpy.ops.wm.save_as_mainfile(filepath=str(partial), compress=True, relative_remap=True)
    partial.replace(out)
    return counts


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    result = build(Path(args[0]), Path(args[1]), args[2])
    print("MAP BUILT", result)
