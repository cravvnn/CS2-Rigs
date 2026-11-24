"""Put a glove finish on an agent's hands.

Every agent carries its default gloves as part of its model. A glove finish
belongs to a glove model, so applying one hides the default gloves and puts the
glove model's mesh on the agent's own skeleton. The finish's colour and surface
textures are composed from the game's glove shader inputs, once for each hand.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
SHEEN_NODE = "CS2Rig Sheen"  # the image node that feeds the cloth sheen


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


def _hand_files(session: Session, kit: skins_mod.PaintKit, left: bool, wear: float, seed: int) -> tuple[dict, Path]:
    key = f"glove|{kit.name}|{'left' if left else 'right'}|{wear:.4f}|{seed}|{TEXTURE_SIZE}|{gloves_mod.VERSION}|{composite.VERSION}"
    out_dir = session.cache.finish_dir(key)
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png", "normal": out_dir / "normal.png"}
    return files, out_dir / "sheen.png"  # the sheen is only written for a finish with cloth on it


def _compose_hands(session: Session, kit: skins_mod.PaintKit, hands: set[bool], wear: float, seed: int) -> dict[bool, dict]:
    """Image files for each hand's finish (keyed by `left`), composed once and then reused.

    The game's textures are read here, then both hands are composed side by
    side (numpy lets the other run while it works), then saved here.
    """
    loaded: dict = {}  # source images, shared by both hands
    work = {}
    for left in sorted(hands):
        files, sheen = _hand_files(session, kit, left, wear, seed)
        if all(path.is_file() for path in files.values()):
            continue
        values = session.glove_values(kit, left, seed, wear)
        names = gloves_mod.texture_names(values)
        raw_names = gloves_mod.raw_texture_names(values)
        for name in names:
            cache_key = (values[name], name in raw_names)
            if cache_key not in loaded:
                loaded[cache_key] = paint._pixels(session.texture(values[name], raw=name in raw_names))
        work[left] = (values, {name: loaded[(values[name], name in raw_names)] for name in names})
    if work:
        with ThreadPoolExecutor(max_workers=len(work), thread_name_prefix="cs2rig-glove") as pool:
            running = {left: pool.submit(gloves_mod.compose, values, textures, TEXTURE_SIZE, wear) for left, (values, textures) in work.items()}
            results = {left: job.result() for left, job in running.items()}
        for left, result in results.items():
            files, sheen = _hand_files(session, kit, left, wear, seed)
            files["colour"].parent.mkdir(parents=True, exist_ok=True)
            paint._save(files["colour"], result["colour"], colour=True)
            # Occlusion, roughness and metalness packed the way the glTF importer wires them.
            paint._save(files["surface"], np.stack((result["occlusion"], result["roughness"], result["metalness"]), axis=-1), colour=False)
            paint._save(files["normal"], result["normal"], colour=False)
            if result["cloth"].max() > 0.0:
                # Sheen colour (linear) with the cloth mask in alpha.
                paint._save(sheen, np.concatenate((result["sheen"], result["cloth"][..., None]), axis=-1), colour=False)
    out = {}
    for left in hands:
        files, sheen = _hand_files(session, kit, left, wear, seed)
        if sheen.is_file():
            files["sheen"] = sheen
        out[left] = files
    return out


def _set_sheen(material: bpy.types.Material, path) -> None:
    """Give the cloth parts of a glove the game's sheen.

    The game's cloth shading swaps the usual specular for a soft sheen where the
    cloth mask is set. Principled BSDF's sheen is the same kind of lobe, so the
    mask drives its weight and turns the usual specular down to match.
    """
    tree = material.node_tree
    shader = paint._principled(material)
    if shader is None:
        return
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = "Non-Color"
    image.alpha_mode = "CHANNEL_PACKED"
    if image.packed_file is None:
        image.pack()
    node = tree.nodes.new("ShaderNodeTexImage")
    node.name = node.label = SHEEN_NODE
    node.image = image
    node.location = (shader.location.x - 600, shader.location.y - 700)
    colour = paint._image_node(material, "Base Color")
    if colour is not None:
        for link in colour.inputs["Vector"].links:
            tree.links.new(link.from_socket, node.inputs["Vector"])
    tree.links.new(node.outputs["Color"], shader.inputs["Sheen Tint"])
    tree.links.new(node.outputs["Alpha"], shader.inputs["Sheen Weight"])
    for link in shader.inputs["Roughness"].links:
        # The game's sheen uses roughness squared, as Blender's sheen roughness does.
        squared = tree.nodes.new("ShaderNodeMath")
        squared.operation = "MULTIPLY"
        squared.location = (node.location.x + 300, node.location.y + 150)
        tree.links.new(link.from_socket, squared.inputs[0])
        tree.links.new(link.from_socket, squared.inputs[1])
        tree.links.new(squared.outputs["Value"], shader.inputs["Sheen Roughness"])
    specular = tree.nodes.new("ShaderNodeMath")
    specular.operation = "MULTIPLY_ADD"  # (1 - cloth) * level: the usual specular fades out on cloth
    specular.location = (node.location.x + 300, node.location.y - 150)
    specular.inputs[1].default_value = -shader.inputs["Specular IOR Level"].default_value
    specular.inputs[2].default_value = shader.inputs["Specular IOR Level"].default_value
    tree.links.new(node.outputs["Alpha"], specular.inputs[0])
    tree.links.new(specular.outputs["Value"], shader.inputs["Specular IOR Level"])


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
        # The bare forearm that comes with some gloves keeps its own look.
        hands = {
            slot.material: gloves_mod.is_left(name)
            for slot in mesh.material_slots
            if slot.material is not None and (name := paint._game_material(slot.material)).endswith(("_left.vmat", "_right.vmat"))
        }
        composed = _compose_hands(session, kit, set(hands.values()), wear, seed)  # may take a while the first time
        for material, left in hands.items():
            files = composed[left]
            paint.set_images(material, files)
            if "sheen" in files:
                _set_sheen(material, files["sheen"])
            material.name = f"{material.name}.{kit.name}"
    except Exception:
        remove_gloves(armature)
        raise
    _show(default, False)
    _show(mesh, True)
    mesh[WEAR_PROP] = wear
    mesh[SEED_PROP] = seed
    importer._make_active(context, armature)

