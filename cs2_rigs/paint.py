"""Put a finish on the weapon an agent holds.

The finish's colour and surface textures are composed from the game's own
inputs, as the game does it, for a chosen wear and pattern seed. Effects the
game adds at render time (pearlescence, glitter, iridescence) are not shown.
"""

from __future__ import annotations

from pathlib import Path

import bpy
import numpy as np

from . import importer
from .core import composite
from .core import finish as finish_mod
from .core import skins as skins_mod
from .session import Session, SetupError

SKIN_PROP = "cs2rig_skin"  # on the weapon root: the kit it wears
WEAR_PROP = "cs2rig_skin_wear"
SEED_PROP = "cs2rig_skin_seed"
BASE_MATERIAL_PROP = "cs2rig_base_material"  # on a painted material: the material it stands in for
VMAT_PROP = "vmat"  # the game material an imported material came from
TEXTURE_SIZE = 2048
LEGACY_MARK, CURRENT_MARK = "legacy", "_hd"
WEAPON_TEXTURES = ("g_tColor", "g_tMetalness", "g_tAmbientOcclusion", "g_tMasks")
SHAPE_TEXTURES = ("g_tPosition", "g_tSurface")
SHARED_TEXTURES = ("g_tWear", "g_tGrunge")


def _bodies(armature: bpy.types.Object) -> tuple[bpy.types.Object | None, bpy.types.Object | None]:
    """The held weapon's old-layout body and its current one; either can be missing."""
    meshes = [obj for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"]
    legacy = next((obj for obj in meshes if LEGACY_MARK in obj.name), None)
    current = next((obj for obj in meshes if CURRENT_MARK in obj.name), None)
    if current is None:
        current = next((obj for obj in meshes if obj is not legacy), None)
    return legacy, current


def _show_only(shown: bpy.types.Object, other: bpy.types.Object | None) -> None:
    shown.hide_set(False)
    shown.hide_render = False
    if other is not None and other is not shown:
        other.hide_set(True)
        other.hide_render = True


def _principled(material: bpy.types.Material):
    return next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)


def _image_node(material: bpy.types.Material | None, input_name: str):
    """The image texture that feeds one input of a material's shader, through any nodes between."""
    if material is None or material.node_tree is None:
        return None
    shader = _principled(material)
    if shader is None:
        return None
    pending = [link.from_node for link in shader.inputs[input_name].links]
    seen = set()
    while pending:
        node = pending.pop()
        if node.name in seen:
            continue
        seen.add(node.name)
        if node.type == "TEX_IMAGE":
            return node
        pending += [link.from_node for socket in node.inputs for link in socket.links]
    return None


def _game_material(material: bpy.types.Material | None) -> str:
    """The package path of the game material a Blender material was imported from, or ''."""
    source = material.get(VMAT_PROP) if material is not None else None
    try:
        return str(source["Name"])
    except (TypeError, KeyError):
        return ""


def _pixels(path: Path) -> np.ndarray:
    """An image file's stored values, top row first."""
    image = bpy.data.images.load(str(path))
    try:
        image.colorspace_settings.name = "Non-Color"
        image.alpha_mode = "CHANNEL_PACKED"
        width, height = image.size
        if not width or not height:
            raise SetupError(f"{path.name} could not be read.")
        values = np.empty(width * height * image.channels, dtype=np.float32)
        image.pixels.foreach_get(values)
        return values.reshape(height, width, image.channels)[::-1]
    finally:
        bpy.data.images.remove(image)


def _save(path: Path, values: np.ndarray, colour: bool) -> None:
    """Write a (height, width, 3) array, top row first, as an 8-bit PNG."""
    height, width = values.shape[:2]
    image = bpy.data.images.new(path.stem, width, height, alpha=False)
    try:
        if not colour:
            image.colorspace_settings.name = "Non-Color"
        rgba = np.ones((height, width, 4), dtype=np.float32)
        # Blender keeps byte images in display space, so sRGB-encoded values go in unchanged.
        rgba[..., :3] = values[::-1]
        image.pixels.foreach_set(rgba.ravel())
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def _load(path: Path, colour: bool) -> bpy.types.Image:
    image = bpy.data.images.load(str(path), check_existing=True)
    if not colour:
        image.colorspace_settings.name = "Non-Color"
    if image.packed_file is None:
        image.pack()  # so the finish travels with the .blend
    return image


def _compose(session: Session, kit: skins_mod.PaintKit, values: dict, wear: float, out_dir: Path) -> dict:
    style = int(values.get("F_PAINT_STYLE", 0))
    wanted = list(WEAPON_TEXTURES + SHARED_TEXTURES)
    if style in composite.POSITION_STYLES:
        wanted += SHAPE_TEXTURES
    if style in composite.PATTERN_STYLES:
        wanted.append("g_tPattern")
    if values.get("F_ROUGHNESS_TEXTURE"):
        wanted.append("g_tPaintRoughness")
    if values.get("F_METALNESS_TEXTURE"):
        wanted.append("g_tPaintMetalness")
    textures = {name: _pixels(session.texture(values[name])) for name in wanted if values.get(name)}
    missing = [name for name in ("g_tWear", "g_tGrunge", "g_tPattern") if name in wanted and name not in textures]
    if missing:
        raise SetupError(f"{kit.label} is missing a texture it needs ({', '.join(missing)}).")
    params = {name: value for name, value in values.items() if not isinstance(value, str)}
    result = composite.compose(style, params, textures, TEXTURE_SIZE, wear, bool(values.get("F_SEPARATE_CHANNEL_INPUTS")))

    out_dir.mkdir(parents=True, exist_ok=True)
    _save(out_dir / "colour.png", result["colour"], colour=True)
    # Occlusion, roughness and metalness packed the way the glTF importer wires them.
    surface = np.stack((np.ones_like(result["roughness"]), result["roughness"], result["metalness"]), axis=-1)
    _save(out_dir / "surface.png", surface, colour=False)
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png"}
    if values.get("g_tNormal"):
        files["normal"] = session.texture(values["g_tNormal"])
    return files


def finish_textures(session: Session, kit: skins_mod.PaintKit, body_material: str, wear: float, seed: int) -> tuple[dict, list[str]]:
    """Image files for a finish on one weapon body, composed once and then reused.

    Also returns the parts of the finish that are not shown, in words.
    """
    values = session.finish_values(kit, body_material, seed, wear)
    missing = finish_mod.not_shown(values)
    key = f"{kit.name}|{body_material}|{wear:.4f}|{seed}|{TEXTURE_SIZE}|{composite.VERSION}"
    out_dir = session.cache.finish_dir(key)
    marker = out_dir / "normal.txt"
    if (out_dir / "colour.png").is_file() and (out_dir / "surface.png").is_file() and marker.is_file():
        files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png"}
        normal = marker.read_text(encoding="utf-8").strip()
        if normal:
            files["normal"] = Path(normal)
        if all(path.is_file() for path in files.values()):
            return files, missing
    files = _compose(session, kit, values, wear, out_dir)
    marker.write_text(str(files.get("normal", "")), encoding="utf-8")
    return files, missing


def worn_skin(armature: bpy.types.Object) -> str:
    """The name of the kit on the held weapon, or ''."""
    return next((obj[SKIN_PROP] for obj in armature.children_recursive if SKIN_PROP in obj.keys()), "")


def remove_skin(armature: bpy.types.Object) -> None:
    """Give the held weapon its stock look back."""
    for obj in importer.held_weapon_objects(armature):
        for prop in (SKIN_PROP, WEAR_PROP, SEED_PROP):
            if prop in obj.keys():
                del obj[prop]
        for slot in obj.material_slots if obj.type == "MESH" else ():
            painted = slot.material
            if painted is None or BASE_MATERIAL_PROP not in painted.keys():
                continue
            slot.material = bpy.data.materials.get(painted[BASE_MATERIAL_PROP])
            if painted.users == 0:
                bpy.data.materials.remove(painted)
    legacy, current = _bodies(armature)
    if current is not None:
        _show_only(current, legacy)


def apply_skin(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0) -> list[str]:
    """Paint the held weapon. Returns the parts of the finish that are not shown, in words."""
    legacy, current = _bodies(armature)
    body = legacy if skins_mod.uses_legacy_model(kit) and legacy is not None else current or legacy
    if body is None:
        raise RuntimeError("The agent holds no weapon to paint.")
    remove_skin(armature)
    slot = None
    for candidate in body.material_slots:
        source = _game_material(candidate.material)
        # A body also carries small helper materials; the one that takes paint names its paint inputs.
        if source and _image_node(candidate.material, "Base Color") is not None and session.material(source).get("composite_inputs"):
            slot = candidate
            break
    if slot is None:
        raise RuntimeError(f"{body.name} has no material a finish can go on.")
    wear = min(max(wear, kit.wear_min), kit.wear_max)
    files, missing = finish_textures(session, kit, _game_material(slot.material), wear, seed)  # may take a while the first time

    painted = slot.material.copy()
    painted.name = f"{slot.material.name}.{kit.name}"
    painted[BASE_MATERIAL_PROP] = slot.material.name
    slot.material = painted
    tree = painted.node_tree
    shader = _principled(painted)
    colour = _image_node(painted, "Base Color")
    colour.image = _load(files["colour"], colour=True)
    surface = _image_node(painted, "Roughness")
    if surface is None or surface is colour:
        # No packed surface texture to swap: wire one in.
        surface = tree.nodes.new("ShaderNodeTexImage")
        surface.location = (colour.location.x, colour.location.y - 300)
        split = tree.nodes.new("ShaderNodeSeparateColor")
        split.location = (surface.location.x + 300, surface.location.y)
        for link in colour.inputs["Vector"].links:
            tree.links.new(link.from_socket, surface.inputs["Vector"])
        tree.links.new(surface.outputs["Color"], split.inputs["Color"])
        tree.links.new(split.outputs["Green"], shader.inputs["Roughness"])
        tree.links.new(split.outputs["Blue"], shader.inputs["Metallic"])
    surface.image = _load(files["surface"], colour=False)
    if "normal" in files:
        normal = _image_node(painted, "Normal")
        if normal is not None:
            normal.image = _load(files["normal"], colour=False)

    _show_only(body, legacy if body is current else current)
    root = next((obj for obj in importer.held_weapon_objects(armature) if importer.WEAPON_KEY_PROP in obj.keys()), body)
    root[SKIN_PROP] = kit.name
    root[WEAR_PROP] = wear
    root[SEED_PROP] = seed
    return missing
