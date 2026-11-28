"""A CS2 sky HDRI in any scene: the World it becomes, and its live strength and rotation.

The world is a plain Blender environment setup, so it renders in EEVEE and
Cycles and travels inside a saved .blend:

    Texture Coordinate (Generated) -> Mapping -> Environment Texture -> Background -> World Output

The image is packed on apply, so a reopened file keeps its sky even when the
cache is cleared. Strength and rotation are read back from the nodes, which
makes the panel's sliders work on this world and on a loaded map's own
`CS2 Sky <map>` world alike.
"""

from __future__ import annotations

import math

import bpy

from .core import hdri as hdri_core
from .core.hdri import Sky

HDRI_WORLD_PROP = "cs2rig_hdri"
HDRI_KEY_PROP = "cs2rig_hdri_key"
HDRI_LABEL_PROP = "cs2rig_hdri_label"
HDRI_MATERIAL_PROP = "cs2rig_hdri_material"
HDRI_TEXTURE_PROP = "cs2rig_hdri_texture"
HDRI_FILE_PROP = "cs2rig_hdri_file"
HDRI_BASE_STRENGTH_PROP = "cs2rig_hdri_base_strength"
HDRI_BASE_ROTATION_PROP = "cs2rig_hdri_base_rotation"
HDRI_PREV_WORLD_PROP = "cs2rig_hdri_prev_world"

# The names a built map uses on its own sky world, which this module treats the same way.
MAP_NAME_PROP = "cs2rig_map"
MAP_MATERIAL_PROP = "cs2rig_sky_material"
MAP_TEXTURE_PROP = "cs2rig_sky_texture"
MAP_STRENGTH_PROP = "cs2rig_sky_strength"
MAP_ROTATION_PROP = "cs2rig_sky_rotation"

HDRI_WORLD_PREFIX = "CS2 HDRI "
MAP_WORLD_PREFIX = "CS2 Sky "
DEFAULT_STRENGTH = 1.0


def world_name(label: str) -> str:
    return f"{HDRI_WORLD_PREFIX}{label}"


def is_cs2_sky_world(world: bpy.types.World | None) -> bool:
    """True for a world this module made, or a loaded map's own sky world."""
    if world is None:
        return False
    if world.get(HDRI_WORLD_PROP) or world.get(MAP_NAME_PROP):
        return True
    return world.name.startswith(MAP_WORLD_PREFIX)


def has_hdri(scene: bpy.types.Scene) -> bool:
    """True when the scene's world is a CS2 sky this panel can drive."""
    if scene is None or not is_cs2_sky_world(scene.world):
        return False
    return _background(scene.world) is not None


def label(world: bpy.types.World | None) -> str:
    if world is None:
        return ""
    stored = str(world.get(HDRI_LABEL_PROP, ""))
    if stored:
        return stored
    if world.get("cs2rig_world_kind") == "probe":
        probe_label = str(world.get("cs2rig_probe_label", ""))
        if probe_label:
            return probe_label
    game_map = str(world.get(MAP_NAME_PROP, ""))
    if game_map:
        return hdri_core.label_for_map(game_map)
    return world.name.removeprefix(HDRI_WORLD_PREFIX).removeprefix(MAP_WORLD_PREFIX)


def key(world: bpy.types.World | None) -> str:
    """The catalogue key for this world's sky, or "" when it is not in the catalogue."""
    if world is None:
        return ""
    stored = str(world.get(HDRI_KEY_PROP, ""))
    if stored:
        return stored
    for prop in (HDRI_MATERIAL_PROP, MAP_MATERIAL_PROP):
        found = hdri_core.key_for_material(str(world.get(prop, "")))
        if found:
            return found
    return ""


def strength(scene: bpy.types.Scene) -> float:
    """The Background node's strength, or the value the sky was made with."""
    background = _background(scene.world) if scene is not None else None
    if background is None:
        return base_strength(scene.world if scene is not None else None)
    return float(background.inputs["Strength"].default_value)


def base_strength(world: bpy.types.World | None) -> float:
    if world is None:
        return DEFAULT_STRENGTH
    stored = world.get(HDRI_BASE_STRENGTH_PROP)
    if stored is None:
        stored = world.get(MAP_STRENGTH_PROP)
    return float(stored) if stored is not None else DEFAULT_STRENGTH


def _ensure_editable_world(scene: bpy.types.Scene) -> bpy.types.World | None:
    """Make a linked CS2 map/probe world local before mutating its nodes so edits survive save/reopen."""
    world = scene.world if scene is not None else None
    if world is None:
        return None
    if world.library is not None:
        local = world.make_local()
        local.use_fake_user = False
        scene.world = local
        return local
    return world


def set_strength(scene: bpy.types.Scene, value: float) -> bool:
    """Set the Background strength of the active CS2 sky world. True when it changed something."""
    world = _ensure_editable_world(scene)
    background = _background(world) if world is not None else None
    if background is None:
        return False
    background.inputs["Strength"].default_value = max(0.0, float(value))
    return True


def rotation(scene: bpy.types.Scene) -> float:
    """The active CS2 sky's Mapping node Z rotation in degrees."""
    mapping = _mapping(scene.world) if scene is not None else None
    if mapping is None:
        return base_rotation(scene.world if scene is not None else None)
    return math.degrees(float(mapping.inputs["Rotation"].default_value[2]))


def rotation_offset(scene: bpy.types.Scene) -> float:
    """How far the sky has been turned from the orientation its material gives it."""
    if scene is None:
        return 0.0
    return rotation(scene) - base_rotation(scene.world)


def base_rotation(world: bpy.types.World | None) -> float:
    if world is None:
        return hdri_core.DEFAULT_ROTATION
    stored = world.get(HDRI_BASE_ROTATION_PROP)
    if stored is None:
        stored = world.get(MAP_ROTATION_PROP)
    return float(stored) if stored is not None else hdri_core.DEFAULT_ROTATION


def set_rotation(scene: bpy.types.Scene, offset: float) -> bool:
    """Turn the active CS2 sky by `offset` degrees about Z, keeping its authored orientation."""
    world = _ensure_editable_world(scene)
    mapping = _mapping(world) if world is not None else None
    if mapping is None:
        return False
    angle = math.radians(base_rotation(world) + float(offset))
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, angle)
    return True


def apply(scene: bpy.types.Scene, sky: Sky) -> bpy.types.World:
    """Put this sky on the scene, packed and ready to render."""
    previous = scene.world
    world = bpy.data.worlds.get(world_name(sky.label))
    if world is None or not world.get(HDRI_WORLD_PROP):
        world = bpy.data.worlds.new(world_name(sky.label))
    if previous is not None and previous is not world:
        if previous.get(HDRI_WORLD_PROP):
            world[HDRI_PREV_WORLD_PROP] = str(previous.get(HDRI_PREV_WORLD_PROP, ""))
        else:
            world[HDRI_PREV_WORLD_PROP] = previous.name
    elif previous is None:
        world[HDRI_PREV_WORLD_PROP] = ""

    image = _load_image(sky)
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()

    coord = tree.nodes.new("ShaderNodeTexCoord")
    coord.location = (-900.0, 0.0)
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.name = "CS2 HDRI Rotation"
    mapping.label = "Rotation"
    mapping.location = (-700.0, 0.0)
    mapping.vector_type = "VECTOR"
    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(sky.rotation))
    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    environment.location = (-460.0, 0.0)
    environment.projection = "EQUIRECTANGULAR"
    environment.interpolation = "Linear"
    environment.image = image
    background = tree.nodes.new("ShaderNodeBackground")
    background.location = (-200.0, 0.0)
    background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    background.inputs["Strength"].default_value = sky.strength
    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (60.0, 0.0)

    tree.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    tree.links.new(environment.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])

    world[HDRI_WORLD_PROP] = True
    world[HDRI_KEY_PROP] = sky.key
    world[HDRI_LABEL_PROP] = sky.label
    world[HDRI_MATERIAL_PROP] = sky.material
    world[HDRI_TEXTURE_PROP] = sky.texture
    world[HDRI_FILE_PROP] = sky.file
    world[HDRI_BASE_STRENGTH_PROP] = float(sky.strength)
    world[HDRI_BASE_ROTATION_PROP] = float(sky.rotation)
    # The same names a built map's sky world carries, so both read alike.
    world[MAP_MATERIAL_PROP] = sky.material
    world[MAP_TEXTURE_PROP] = sky.texture
    world[MAP_STRENGTH_PROP] = float(sky.strength)
    world[MAP_ROTATION_PROP] = float(sky.rotation)
    scene.world = world
    if previous is not None and previous is not world and previous.get(HDRI_WORLD_PROP) and previous.users == 0:
        old_image = _image(previous)
        bpy.data.worlds.remove(previous)
        if old_image is not None and old_image.users == 0:
            bpy.data.images.remove(old_image)
    return world


def remove(scene: bpy.types.Scene) -> bool:
    """Take the active CS2 sky off the scene and restore what was there before."""
    world = scene.world if scene is not None else None
    if not is_cs2_sky_world(world):
        return False
    previous_name = str(world.get(HDRI_PREV_WORLD_PROP, ""))
    previous = bpy.data.worlds.get(previous_name) if previous_name else None
    scene.world = previous if previous is not None and previous is not world else None
    if world.get(HDRI_WORLD_PROP) and world.users == 0:
        image = _image(world)
        bpy.data.worlds.remove(world)
        if image is not None and image.users == 0:
            bpy.data.images.remove(image)
    return True


def _load_image(sky: Sky) -> bpy.types.Image:
    """The sky's EXR, packed so the .blend keeps it without the cache."""
    image = bpy.data.images.load(sky.file, check_existing=True)
    image.name = f"CS2 HDRI {sky.label}"
    try:
        image.colorspace_settings.name = "Linear Rec.709"
    except TypeError:
        pass
    if image.packed_file is None:
        image.pack()
    return image


def _image(world: bpy.types.World) -> bpy.types.Image | None:
    tree = world.node_tree
    if tree is None:
        return None
    return next((node.image for node in tree.nodes if node.type == "TEX_ENVIRONMENT" and node.image is not None), None)


def _background(world: bpy.types.World | None) -> bpy.types.ShaderNodeBackground | None:
    tree = world.node_tree if world is not None else None
    if tree is None:
        return None
    output = next((node for node in tree.nodes if node.type == "OUTPUT_WORLD"), None)
    node = None
    if output is not None and output.inputs["Surface"].is_linked:
        node = _upstream(output.inputs["Surface"], ("BACKGROUND",))
    if node is not None:
        return node
    return next((node for node in tree.nodes if node.type == "BACKGROUND"), None)


def _mapping(world: bpy.types.World | None) -> bpy.types.ShaderNodeMapping | None:
    tree = world.node_tree if world is not None else None
    if tree is None:
        return None
    environment = next((node for node in tree.nodes if node.type == "TEX_ENVIRONMENT"), None)
    if environment is not None and environment.inputs["Vector"].is_linked:
        node = _upstream(environment.inputs["Vector"], ("MAPPING",))
        if node is not None:
            return node
    return next((node for node in tree.nodes if node.type == "MAPPING"), None)


def _upstream(socket, wanted: tuple[str, ...], depth: int = 8):
    """Follow a linked input back through shader nodes until one of `wanted` types is found."""
    node = socket.links[0].from_node if socket.links else None
    while node is not None and depth > 0:
        if node.type in wanted:
            return node
        following = None
        for entry in node.inputs:
            if entry.is_linked:
                following = entry.links[0].from_node
                break
        node = following
        depth -= 1
    return None
