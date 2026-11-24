"""Put a glove finish on an agent's hands.

Every agent carries its default gloves as part of its model. A glove finish
belongs to a glove model, so applying one hides the default gloves and puts the
glove model's mesh on the agent's own skeleton. The finish's colour and surface
textures are composed from the game's glove shader inputs, once for each hand.
"""

from __future__ import annotations

import bpy
import numpy as np

from . import importer, paint
from .core import catalog as catalog_mod
from .core import composite
from .core import gloves as gloves_mod
from .core import skins as skins_mod
from .session import Session, SetupError

GLOVE_PROP = "cs2rig_gloves"  # on the glove mesh: the finish it wears
WEAR_PROP = "cs2rig_gloves_wear"
SEED_PROP = "cs2rig_gloves_seed"
DEFAULT_GLOVES = "thirdperson_default_gloves"  # the agent's own mesh for its hands
MESH_PART = "worldmodel"  # the third-person mesh of a glove model
TEXTURE_SIZE = 2048


def _default_gloves(armature: bpy.types.Object) -> bpy.types.Object | None:
    return next((obj for obj in armature.children_recursive if obj.type == "MESH" and DEFAULT_GLOVES in obj.name), None)


def _glove_meshes(armature: bpy.types.Object) -> list[bpy.types.Object]:
    return [obj for obj in armature.children_recursive if GLOVE_PROP in obj.keys()]


def worn_gloves(armature: bpy.types.Object) -> str:
    """The name of the glove finish on the agent, or ''."""
    return next((obj[GLOVE_PROP] for obj in _glove_meshes(armature)), "")


def _show(obj: bpy.types.Object, shown: bool) -> None:
    obj.hide_set(not shown)
    obj.hide_render = not shown


def remove_gloves(armature: bpy.types.Object) -> None:
    """Give the agent its default gloves back."""
    for obj in _glove_meshes(armature):
        materials = [slot.material for slot in obj.material_slots if slot.material is not None]
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for material in materials:
            if material.users == 0:
                images = {node.image for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image is not None}
                bpy.data.materials.remove(material)
                for image in images:
                    if image.users == 0:
                        bpy.data.images.remove(image)
    default = _default_gloves(armature)
    if default is not None:
        _show(default, True)


def _bring_in(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit) -> bpy.types.Object:
    """Import the glove model and keep only its third-person mesh, put on the agent's skeleton."""
    item = gloves_mod.glove_item(kit)
    glb = session.export_model(gloves_mod.model_path(kit), session.cache.model_dir(f"glove/{item}"), [catalog_mod.BASE_CLIP], materials=True)
    actions_before = set(bpy.data.actions)
    objects = importer._import_glb(context, glb)
    for action in [a for a in bpy.data.actions if a not in actions_before]:
        bpy.data.actions.remove(action)
    mesh = next((obj for obj in objects if obj.type == "MESH" and MESH_PART in obj.name), None)
    if mesh is None:
        bpy.data.batch_remove(objects)
        raise RuntimeError(f"{glb.name} has no third-person glove mesh.")
    datas = {obj.data for obj in objects if obj is not mesh}
    bpy.data.batch_remove([obj for obj in objects if obj is not mesh])
    for data in datas:
        if data.users == 0 and isinstance(data, (bpy.types.Mesh, bpy.types.Armature)):
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.armatures).remove(data)

    # The glove skeleton is the agent's own bones under the same names, so the weights carry over.
    for old in list(mesh.users_collection):
        old.objects.unlink(mesh)
    armature.users_collection[0].objects.link(mesh)
    mesh.parent = armature
    mesh.matrix_parent_inverse.identity()
    for modifier in mesh.modifiers:
        if modifier.type == "ARMATURE":
            modifier.object = armature
    mesh.name = f"{armature.name.removesuffix('.skeleton')}.gloves"
    return mesh


def _compose_hand(session: Session, kit: skins_mod.PaintKit, left: bool, wear: float, seed: int, loaded: dict) -> dict:
    """Image files for one hand's finish, composed once and then reused."""
    values = session.glove_values(kit, left, seed, wear)
    key = f"glove|{kit.name}|{'left' if left else 'right'}|{wear:.4f}|{seed}|{TEXTURE_SIZE}|{gloves_mod.VERSION}|{composite.VERSION}"
    out_dir = session.cache.finish_dir(key)
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png"}
    if not all(path.is_file() for path in files.values()):
        for name in gloves_mod.texture_names(values):
            path = values[name]
            if path not in loaded:
                loaded[path] = paint._pixels(session.texture(path))
        result = gloves_mod.compose(values, {name: loaded[values[name]] for name in gloves_mod.texture_names(values)}, TEXTURE_SIZE, wear)
        out_dir.mkdir(parents=True, exist_ok=True)
        paint._save(files["colour"], result["colour"], colour=True)
        # Occlusion, roughness and metalness packed the way the glTF importer wires them.
        paint._save(files["surface"], np.stack((np.ones_like(result["roughness"]), result["roughness"], result["metalness"]), axis=-1), colour=False)
    if values.get("g_tNormal"):
        files["normal"] = session.texture(values["g_tNormal"])
    return files


def apply_gloves(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0) -> None:
    """Dress the agent's hands in a glove finish."""
    if not gloves_mod.is_glove_kit(kit):
        raise SetupError(f"{kit.label} is not a glove finish.")
    default = _default_gloves(armature)
    if default is None:
        raise RuntimeError("This agent has no gloves mesh to replace.")
    wear = min(max(wear, kit.wear_min), kit.wear_max)
    remove_gloves(armature)
    mesh = _bring_in(context, session, armature, kit)
    mesh[GLOVE_PROP] = kit.name
    try:
        loaded: dict = {}  # source images, kept while both hands are composed
        for slot in mesh.material_slots:
            material = slot.material
            name = paint._game_material(material)
            if not name.endswith(("_left.vmat", "_right.vmat")):
                continue  # the bare forearm that comes with some gloves keeps its own look
            files = _compose_hand(session, kit, gloves_mod.is_left(name), wear, seed, loaded)  # may take a while the first time
            paint.set_images(material, files)
            material.name = f"{material.name}.{kit.name}"
    except Exception:
        remove_gloves(armature)
        raise
    _show(default, False)
    _show(mesh, True)
    mesh[WEAR_PROP] = wear
    mesh[SEED_PROP] = seed
    importer._make_active(context, armature)

