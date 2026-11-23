"""Put a painted finish on the weapon an agent holds.

Only finishes that are one texture painted in the weapon's own layout are
handled, shown as new: no wear, and none of the styles the game builds up from
patterns, colours and masks.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from . import importer
from .core import skins as skins_mod
from .session import Session

SKIN_PROP = "cs2rig_skin"  # on the weapon root: the kit it wears
BASE_MATERIAL_PROP = "cs2rig_base_material"  # on a painted material: the material it stands in for
PAINT_ROUGHNESS = 0.5  # for kits that bring no roughness texture
LEGACY_MARK, CURRENT_MARK = "legacy", "_hd"


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

def _colour_node(material: bpy.types.Material | None):
    """The image that feeds a material's base colour, skipping one-pixel filler materials."""
    if material is None or material.node_tree is None:
        return None
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image and max(node.image.size) > 8:
            if any(link.to_socket.name == "Base Color" for out in node.outputs for link in out.links):
                return node
    return None


def _principled(material: bpy.types.Material):
    return next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)


def _load(path: Path, colour: bool) -> bpy.types.Image:
    image = bpy.data.images.load(str(path), check_existing=True)
    if not colour:
        image.colorspace_settings.name = "Non-Color"
    return image


def _unlink(tree: bpy.types.NodeTree, socket) -> None:
    for link in list(socket.links):
        tree.links.remove(link)


def worn_skin(armature: bpy.types.Object) -> str:
    """The name of the kit on the held weapon, or ''."""
    return next((obj[SKIN_PROP] for obj in armature.children_recursive if SKIN_PROP in obj.keys()), "")


def remove_skin(armature: bpy.types.Object) -> None:
    """Give the held weapon its stock look back."""
    for obj in importer.held_weapon_objects(armature):
        if SKIN_PROP in obj.keys():
            del obj[SKIN_PROP]
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


def apply_skin(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit) -> None:
    legacy, current = _bodies(armature)
    body = legacy if skins_mod.uses_legacy_model(kit) and legacy is not None else current or legacy
    if body is None:
        raise RuntimeError("The agent holds no weapon to paint.")
    textures = session.paint_textures(kit)  # converts; may take a few seconds the first time
    remove_skin(armature)
    slot = next((s for s in body.material_slots if _colour_node(s.material) is not None), None)
    if slot is None:
        raise RuntimeError(f"{body.name} has no textured material to paint.")

    painted = slot.material.copy()
    painted.name = f"{slot.material.name}.{kit.name}"
    painted[BASE_MATERIAL_PROP] = slot.material.name
    slot.material = painted
    tree = painted.node_tree
    colour = _colour_node(painted)
    colour.image = _load(textures["colour"], colour=True)
    shader = _principled(painted)
    if shader is not None:
        # Paint covers the metal; the stock metalness would make it mirror-like.
        _unlink(tree, shader.inputs["Metallic"])
        shader.inputs["Metallic"].default_value = 0.0
        _unlink(tree, shader.inputs["Roughness"])
        shader.inputs["Roughness"].default_value = PAINT_ROUGHNESS
        if "roughness" in textures:
            rough = tree.nodes.new("ShaderNodeTexImage")
            rough.image = _load(textures["roughness"], colour=False)
            rough.location = (colour.location.x, colour.location.y - 300)
            for link in colour.inputs["Vector"].links:  # same layout as the colour texture
                tree.links.new(link.from_socket, rough.inputs["Vector"])
            tree.links.new(rough.outputs["Color"], shader.inputs["Roughness"])
    if "normal" in textures:
        for node in tree.nodes:
            if node.type == "NORMAL_MAP":
                for link in node.inputs["Color"].links:
                    if link.from_node.type == "TEX_IMAGE":
                        link.from_node.image = _load(textures["normal"], colour=False)

    _show_only(body, legacy if body is current else current)
    root = next((obj for obj in importer.held_weapon_objects(armature) if importer.WEAPON_KEY_PROP in obj.keys()), body)
    root[SKIN_PROP] = kit.name
