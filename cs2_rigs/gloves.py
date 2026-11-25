"""Put a glove finish on an agent's hands.

Every agent carries its default gloves as part of its model. A glove finish
belongs to a glove model, so applying one hides the default gloves and puts the
glove model's mesh on the agent's own skeleton. The finish's colour and surface
textures are composed from the game's glove shader inputs, once for each hand.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bpy
import numpy as np

from . import importer, paint
from .core import catalog as catalog_mod
from .core import composite
from .core import gloves as gloves_mod
from .core import png
from .core import skins as skins_mod
from .session import Session, SetupError

GLOVE_PROP = "cs2rig_gloves"  # on the glove mesh: the finish it wears
WEAR_PROP = "cs2rig_gloves_wear"
SEED_PROP = "cs2rig_gloves_seed"
DEFAULT_GLOVES = "thirdperson_default_gloves"  # the agent's own mesh for its hands
MESH_PART = "worldmodel"  # the third-person mesh of a glove model
TEXTURE_SIZE = 2048
EXPORT_WORKERS = 4  # textures converted at once; the converter's staging is per process and thread
SHEEN_NODE = "CS2Rig Sheen"  # the image node that feeds the cloth sheen


def _default_gloves(armature: bpy.types.Object) -> bpy.types.Object | None:
    return next((obj for obj in armature.children_recursive if obj.type == "MESH" and DEFAULT_GLOVES in obj.name), None)


def _glove_meshes(armature: bpy.types.Object) -> list[bpy.types.Object]:
    return [obj for obj in armature.children_recursive if GLOVE_PROP in obj.keys()]


def worn_gloves(armature: bpy.types.Object) -> str:
    """The name of the glove finish on the agent, or ''."""
    return next((obj[GLOVE_PROP] for obj in _glove_meshes(armature)), "")


def worn_glove_settings(armature: bpy.types.Object) -> tuple[str, float, int] | None:
    """The glove finish on the agent with its wear and seed, or None."""
    mesh = next(iter(_glove_meshes(armature)), None)
    if mesh is None:
        return None
    return mesh[GLOVE_PROP], float(mesh.get(WEAR_PROP, 0.0)), int(mesh.get(SEED_PROP, 0))


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


def _export_model(session: Session, kit: skins_mod.PaintKit) -> Path:
    """The glove model converted from the game (cached after the first time). Needs no Blender."""
    item = gloves_mod.glove_item(kit)
    return session.export_model(gloves_mod.model_path(kit), session.cache.model_dir(f"glove/{item}"), [catalog_mod.BASE_CLIP], materials=True)


def _bring_in(context: bpy.types.Context, glb: Path, armature: bpy.types.Object) -> bpy.types.Object:
    """Import the glove model and keep only its third-person mesh, put on the agent's skeleton."""
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
    (armature.users_collection or [context.scene.collection])[0].objects.link(mesh)
    mesh.parent = armature
    mesh.matrix_parent_inverse.identity()
    for modifier in mesh.modifiers:
        if modifier.type == "ARMATURE":
            modifier.object = armature
    mesh.name = f"{armature.name.removesuffix('.skeleton')}.gloves"
    return mesh


def _hand_files(session: Session, kit: skins_mod.PaintKit, left: bool, wear: float, seed: int) -> tuple[dict, Path]:
    wear = round(wear, 3)
    key = f"glove|{kit.name}|{'left' if left else 'right'}|{wear:.3f}|{seed}|{TEXTURE_SIZE}|{gloves_mod.VERSION}|{composite.VERSION}"
    out_dir = session.cache.finish_dir(key)
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png", "normal": out_dir / "normal.png"}
    return files, out_dir / "sheen.png"  # the sheen is only written for a finish with cloth on it


HANDS = (True, False)  # left, right


def _compose_and_save(session: Session, kit: skins_mod.PaintKit, wear: float, seed: int, work: dict[bool, tuple[dict, dict]]) -> None:
    """Compose each hand's finish and save its images. Needs no Blender.

    The hands are composed one after the other: each one needs several GB of
    memory at full size, and composing both at once roughly doubled the peak.
    A hand's images are saved while the next hand is composed.
    """
    wear = round(wear, 3)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="cs2rig-glove-save") as saver:
        saving = []
        for left in list(work):
            values, textures = work.pop(left)
            result = gloves_mod.compose(values, textures, TEXTURE_SIZE, wear)
            saving.append(saver.submit(_save_hand, _hand_files(session, kit, left, wear, seed), result))
            del result
        for job in saving:
            job.result()  # raises a failed save here


def _save_hand(paths: tuple[dict, Path], result: dict) -> None:
    files, sheen = paths
    files["colour"].parent.mkdir(parents=True, exist_ok=True)
    png.write(files["colour"], result["colour"])
    # Occlusion, roughness and metalness packed the way the glTF importer wires them.
    png.write(files["surface"], np.stack((result["occlusion"], result["roughness"], result["metalness"]), axis=-1))
    if result["cloth"].max() > 0.0:
        # Sheen colour (linear) with the cloth mask in alpha.
        png.write(sheen, np.concatenate((result["sheen"], result["cloth"][..., None]), axis=-1))
    # Written last: a hand counts as made once all three of its files are there.
    png.write(files["normal"], result["normal"])


class GloveJob:
    """Gets a glove finish ready, with the slow parts on a worker thread.

    Converting the game's files, composing both hands and saving their images
    happen off the main thread, so Blender stays usable. The two short steps
    that need Blender, reading the game's textures and putting the gloves on,
    run in `step`, which the main thread calls until it returns True.
    """

    def __init__(self, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0):
        if not gloves_mod.is_glove_kit(kit):
            raise SetupError(f"{kit.label} is not a glove finish.")
        if _default_gloves(armature) is None:
            raise RuntimeError("This agent has no gloves mesh to replace.")
        self.session, self.kit, self.seed = session, kit, seed
        self.wear = min(max(wear, kit.wear_min), kit.wear_max)
        self.armature_name = armature.name
        self.stage = "Converting the game's files"
        self.cancelled = False
        self._phase = "prepare"
        self._error: BaseException | None = None
        self._glb: Path | None = None
        self._paths: dict[bool, tuple[dict, dict]] = {}  # hand -> (values, {texture name: (file, raw)})
        self._work: dict[bool, tuple[dict, dict]] = {}  # hand -> (values, {texture name: pixels})
        self._run(self._prepare)

    def _run(self, work) -> None:
        def guarded():
            try:
                work()
            except BaseException as exc:  # raised again on the main thread
                self._error = exc
        self._thread = threading.Thread(target=guarded, name="cs2rig-gloves", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        return self._thread.is_alive()

    def wait(self) -> None:
        self._thread.join()

    def _prepare(self) -> None:
        self._glb = _export_model(self.session, self.kit)
        wear = round(self.wear, 3)
        values_by_hand: dict[bool, dict] = {}
        pending: dict[tuple[str, bool], None] = {}
        for left in HANDS:
            files, _sheen = _hand_files(self.session, self.kit, left, wear, self.seed)
            if all(path.is_file() for path in files.values()):
                continue
            values = self.session.glove_values(self.kit, left, self.seed, wear)
            values_by_hand[left] = values
            raw_names = gloves_mod.raw_texture_names(values)
            for name in gloves_mod.texture_names(values):
                pending[(values[name], name in raw_names)] = None  # each texture converted once for both hands
        exported: dict[tuple[str, bool], Path] = {}
        if pending:
            with ThreadPoolExecutor(max_workers=EXPORT_WORKERS, thread_name_prefix="cs2rig-texture") as pool:
                futures = {key: pool.submit(self.session.texture, key[0], raw=key[1]) for key in pending}
                for key, future in futures.items():
                    exported[key] = future.result()
        for left, values in values_by_hand.items():
            raw_names = gloves_mod.raw_texture_names(values)
            self._paths[left] = (values, {name: (exported[(values[name], name in raw_names)], name in raw_names) for name in gloves_mod.texture_names(values)})

    def _read_textures(self) -> None:
        loaded: dict = {}  # each file read once, shared by both hands
        for left, (values, paths) in self._paths.items():
            for key in paths.values():
                if key not in loaded:
                    loaded[key] = paint._pixels(key[0])
            self._work[left] = (values, {name: loaded[key] for name, key in paths.items()})
        self._paths = {}

    def _compose(self) -> None:
        _compose_and_save(self.session, self.kit, self.wear, self.seed, self._work)
        self._work = {}

    def step(self, context: bpy.types.Context) -> bool:
        """Move the job on from the main thread. True once the gloves are on."""
        if self.busy:
            return False
        if self._error is not None:
            raise self._error
        if self.cancelled:
            return True
        if self._phase == "prepare":
            self._read_textures()
            self._phase, self.stage = "compose", "Composing the finish"
            self._run(self._compose)
            return False
        armature = bpy.data.objects.get(self.armature_name)
        if armature is None or armature.type != "ARMATURE":
            raise RuntimeError("The agent was removed before its gloves were ready.")
        _put_on(context, self.session, armature, self.kit, self._glb, self.wear, self.seed)
        self._phase = "done"
        return True


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


def _put_on(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, glb: Path, wear: float, seed: int) -> None:
    """Swap the agent's gloves for the glove model wearing the composed finish."""
    default = _default_gloves(armature)
    if default is None:
        raise RuntimeError("This agent has no gloves mesh to replace.")
    remove_gloves(armature)
    mesh = _bring_in(context, glb, armature)
    mesh[GLOVE_PROP] = kit.name
    try:
        # The bare forearm that comes with some gloves keeps its own look.
        hands = {
            slot.material: gloves_mod.is_left(name)
            for slot in mesh.material_slots
            if slot.material is not None and (name := paint._game_material(slot.material)).endswith(("_left.vmat", "_right.vmat"))
        }
        for material, left in hands.items():
            files, sheen = _hand_files(session, kit, left, wear, seed)
            paint.set_images(material, files)
            if sheen.is_file():
                _set_sheen(material, sheen)
            material.name = f"{material.name}.{kit.name}"
    except Exception:
        remove_gloves(armature)
        raise
    _show(default, False)
    _show(mesh, True)
    mesh[WEAR_PROP] = wear
    mesh[SEED_PROP] = seed
    importer._make_active(context, armature)


def apply_gloves(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0) -> None:
    """Dress the agent's hands in a glove finish, waiting for it to be ready."""
    job = GloveJob(session, armature, kit, wear, seed)
    while not job.step(context):
        job.wait()
