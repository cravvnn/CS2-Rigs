"""Turn a converted map .glb into a library .blend. Runs inside a background Blender:

    blender --background --factory-startup --python map_build.py -- <map.glb> <out.blend> <name> [extras.json]

A map is millions of triangles and hundreds of textures, so it is built once into
its own file and scenes link to it instead of carrying a copy.

`extras.json` is written by `core.maplights.extract`: it names the map's real sky
EXR and lists its calibrated lights. The sky image is packed into the library,
and the lights go into a `<name>_lights` sub-collection, so a linked map carries
both with it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

if __package__:
    from .core import maplights
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from core import maplights  # noqa: E402

MAP_NAME_PROP = "cs2rig_map"
SKY_WORLD_PROP = "cs2rig_sky_world"
LIGHT_CLASS_PROP = "cs2rig_light_class"
LIGHT_TARGET_PROP = "cs2rig_light_target"
MAP_EXTRAS_VERSION = maplights.MANIFEST_VERSION

# Collections inside a map, in display order. The lights collection is named
# `<map>_lights` so it is easy to hide for a fast geometry-only render.
GEOMETRY, OVERLAYS, LIGHTS = "Geometry", "Overlays", "Lights"
LIGHTS_SUFFIX = "_lights"
# The converter names decal-like meshes after the overlay they came from.
OVERLAY_MARKER = "_overlay"

PLAIN_SKY_COLOUR = (0.45, 0.62, 0.85)
PLAIN_SKY_STRENGTH = 0.3


def collection_name(name: str, group: str) -> str:
    return f"{name}{LIGHTS_SUFFIX}" if group == LIGHTS else f"{name} {group}"


def _group_for(obj: bpy.types.Object) -> str:
    if obj.type == "LIGHT":
        return LIGHTS
    if OVERLAY_MARKER in obj.name:
        return OVERLAYS
    return GEOMETRY


def _read_extras(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == MAP_EXTRAS_VERSION else None


def build(glb: Path, out: Path, name: str, extras_path: Path | None = None) -> dict[str, int]:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb), import_shading="NORMALS", disable_bone_shape=True)
    scene = bpy.context.scene
    extras = _read_extras(extras_path)

    if extras is not None and extras.get("lights"):
        # The glTF exporter only carries light_environment, and its node is a
        # stand-in for the entity lump. The entities know every lamp, so the
        # proxy is replaced rather than doubled.
        _remove_gltf_lights()

    objects = list(scene.objects)
    if not any(obj.type == "MESH" for obj in objects):
        raise RuntimeError(f"{glb.name} has no geometry.")

    top = bpy.data.collections.new(name)
    top[MAP_NAME_PROP] = name
    scene.collection.children.link(top)
    groups: dict[str, bpy.types.Collection] = {}
    counts: dict[str, int] = {}

    def group_for(group: str) -> bpy.types.Collection:
        if group not in groups:
            collection = bpy.data.collections.new(collection_name(name, group))
            top.children.link(collection)
            groups[group] = collection
        return groups[group]

    for obj in objects:
        group = _group_for(obj)
        target = group_for(group)
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        target.objects.link(obj)
        counts[group] = counts.get(group, 0) + 1

    # Overlays are thin decals lying on other surfaces; their shadows only add noise.
    for obj in groups[OVERLAYS].objects if OVERLAYS in groups else ():
        obj.visible_shadow = False

    if extras is not None and extras.get("lights"):
        counts[LIGHTS] = _add_lights(group_for(LIGHTS), extras["lights"])

    world = _add_sky_world(extras.get("sky") if extras is not None else None, name)
    if world is not None:
        top[SKY_WORLD_PROP] = world.name
    top["cs2rig_light_count"] = counts.get(LIGHTS, 0)

    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    # Textures stay as files next to the .glb; relative paths keep the folder movable.
    bpy.ops.wm.save_as_mainfile(filepath=str(partial), compress=True, relative_remap=True)
    partial.replace(out)
    return counts


def _remove_gltf_lights() -> None:
    for obj in [obj for obj in bpy.context.scene.objects if obj.type == "LIGHT"]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)


def _add_lights(collection: bpy.types.Collection, lights: list[dict]) -> int:
    added = 0
    for light in lights:
        data = _light_data(light)
        if data is None:
            continue
        obj = bpy.data.objects.new(str(light.get("name") or light.get("classname") or "light"), data)
        collection.objects.link(obj)
        obj.location = maplights.blender_position(tuple(light.get("origin") or (0.0, 0.0, 0.0)))
        x, y, z = maplights.blender_light_axes(tuple(light.get("angles") or (0.0, 0.0, 0.0)))
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Matrix((x, y, z)).transposed().to_quaternion()
        obj[LIGHT_CLASS_PROP] = str(light.get("classname") or "")
        if light.get("name"):
            obj[LIGHT_TARGET_PROP] = str(light["name"])
        added += 1
    return added


def _light_data(light: dict) -> bpy.types.Light | None:
    kind = light.get("kind")
    name = str(light.get("name") or light.get("classname") or "light")
    size = light.get("size") or (0.0, 0.0)
    size_x, size_y = float(size[0]), float(size[1])
    soft = min(0.25, max(0.001, min(size_x, size_y) * 0.5))
    cutoff = max(0.0, float(light.get("cutoff") or 0.0))

    if kind == "sun":
        data = bpy.data.lights.new(name, type="SUN")
        data.angle = math.radians(max(0.0, float(light.get("angular_diameter") or 0.53)))
    elif kind == "area":
        data = bpy.data.lights.new(name, type="AREA")
        data.shape = _area_shape(light)
        data.size = max(size_x, size_y)
        data.size_y = size_y
    elif kind == "spot":
        data = bpy.data.lights.new(name, type="SPOT")
        data.spot_size = math.radians(max(1.0, min(179.0, float(light.get("spot_size") or 45.0))))
        data.spot_blend = max(0.0, min(1.0, float(light.get("spot_blend") or 0.0)))
        data.shadow_soft_size = soft
    else:
        data = bpy.data.lights.new(name, type="POINT")
        data.shadow_soft_size = soft

    data.energy = max(0.0, float(light.get("energy") or 0.0))
    data.color = tuple(light.get("color") or (1.0, 1.0, 1.0))[:3]
    data.use_shadow = bool(light.get("shadow", True))
    if hasattr(data, "cutoff_distance") and kind != "sun":
        data.cutoff_distance = cutoff
    return data


def _area_shape(light: dict) -> str:
    shape = int(light.get("area_shape") or 0)
    if shape == 1:
        return "ELLIPSE"
    if shape == 2:
        return "DISK"
    if shape == 3:
        return "SQUARE"
    return "RECTANGLE"


def _add_sky_world(sky: dict | None, name: str) -> bpy.types.World | None:
    """Build and pack the map's real sky, or return None when it has none."""
    if not sky or not sky.get("file"):
        return None
    path = Path(str(sky["file"]))
    if not path.is_file():
        return None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:
        return None
    width, height = image.size
    if width != height * 2:
        # Only lat-long skies can drive Blender's equirectangular environment.
        bpy.data.images.remove(image)
        print("MAP SKY skipped: not a 2:1 equirectangular image", width, height)
        return None
    image.name = f"CS2 Sky {name}"
    try:
        image.colorspace_settings.name = "Linear Rec.709"
    except TypeError:
        pass
    image.pack()

    world = bpy.data.worlds.new(f"CS2 Sky {name}")
    world.use_fake_user = True  # the library must keep it without a scene using it
    world[MAP_NAME_PROP] = name
    world["cs2rig_sky_material"] = str(sky.get("material") or "")
    world["cs2rig_sky_texture"] = str(sky.get("texture") or "")
    world["cs2rig_sky_rotation"] = float(sky.get("rotation") or 0.0)
    world["cs2rig_sky_strength"] = float(sky.get("strength") or 0.0)
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()

    coord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.vector_type = "VECTOR"
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(float(sky.get("rotation") or 0.0)))
    environment = nodes.new("ShaderNodeTexEnvironment")
    environment.projection = "EQUIRECTANGULAR"
    environment.interpolation = "Linear"
    environment.image = image
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = float(sky.get("strength") or 1.0)
    output = nodes.new("ShaderNodeOutputWorld")

    tint = tuple(sky.get("tint") or (1.0, 1.0, 1.0))[:3]
    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    if any(abs(channel - 1.0) > 1e-3 for channel in tint):
        multiply = nodes.new("ShaderNodeVectorMath")
        multiply.operation = "MULTIPLY"
        multiply.inputs[1].default_value = tint
        links.new(environment.outputs["Color"], multiply.inputs[0])
        links.new(multiply.outputs["Vector"], background.inputs["Color"])
    else:
        links.new(environment.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    return world


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    result = build(Path(args[0]), Path(args[1]), args[2], Path(args[3]) if len(args) > 3 else None)
    print("MAP BUILT", result)
