"""Put a finish on the weapon an agent holds.

The finish's colour and surface textures are composed from the game's own
inputs, as the game does it, for a chosen wear and pattern seed. Effects the
game adds at render time (pearlescence, glitter, iridescence) are not shown.
"""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bpy
import numpy as np

from . import importer
from .core import composite
from .core import finish as finish_mod
from .core import png
from .core import proc
from .core import skins as skins_mod
from .session import Session, SetupError

SKIN_PROP = "cs2rig_skin"  # on the weapon root: the kit it wears
WEAR_PROP = "cs2rig_skin_wear"
SEED_PROP = "cs2rig_skin_seed"
BASE_MATERIAL_PROP = "cs2rig_base_material"  # on a painted material: the material it stands in for
VMAT_PROP = "vmat"  # the game material an imported material came from
TEXTURE_SIZE = 2048
EXPORT_WORKERS = 4  # textures converted at once; the converter's staging is per process and thread
LEGACY_MARK, CURRENT_MARK = "legacy", "_hd"
WEAPON_TEXTURES = ("g_tColor", "g_tMetalness", "g_tAmbientOcclusion", "g_tMasks")
SHAPE_TEXTURES = ("g_tPosition", "g_tSurface")
SHARED_TEXTURES = ("g_tWear", "g_tGrunge")


def _bodies(armature: bpy.types.Object) -> tuple[bpy.types.Object | None, bpy.types.Object | None]:
    """The held weapon's old-layout body and its current one; either can be missing.

    Files imported by 0.3.0 carry the role on each body, so renaming one does not
    lose it; older files fall back to the names the importer gave them.
    """
    meshes = [obj for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"]
    legacy = next((obj for obj in meshes if obj.get(importer.BODY_KIND_PROP) == "legacy"), None)
    current = next((obj for obj in meshes if obj.get(importer.BODY_KIND_PROP) == "current"), None)
    if legacy is None:
        legacy = next((obj for obj in meshes if LEGACY_MARK in obj.name), None)
    if current is None:
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


def _load(path: Path, colour: bool) -> bpy.types.Image:
    image = bpy.data.images.load(str(path), check_existing=True)
    if not colour:
        image.colorspace_settings.name = "Non-Color"
    if image.packed_file is None:
        image.pack()  # so the finish travels with the .blend
    return image


def _finish_key(kit: skins_mod.PaintKit, body_material: str, wear: float, seed: int) -> str:
    """What one finish's images are composed from; the wear is rounded so a slider drag reuses entries."""
    return f"{kit.name}|{body_material}|{wear:.3f}|{seed}|{TEXTURE_SIZE}|{composite.VERSION}"


def _cached_finish(out_dir: Path) -> dict | None:
    """The images of an already-composed finish, or None when one is missing."""
    marker = out_dir / "normal.txt"
    if not ((out_dir / "colour.png").is_file() and (out_dir / "surface.png").is_file() and marker.is_file()):
        return None
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png"}
    normal = marker.read_text(encoding="utf-8").strip()
    if normal:
        files["normal"] = Path(normal)
    return files if all(path.is_file() for path in files.values()) else None


def _wanted_textures(values: dict) -> list[str]:
    """The finish's textures the compositor reads, by shader name."""
    style = int(values.get("F_PAINT_STYLE", 0))
    case_hardening = bool(values.get("F_CASE_HARDENING")) and style in (composite.SOLID, composite.ANTIQUED, composite.GUNSMITH)
    ch_trilinear = case_hardening and bool(values.get("F_CASE_HARDENING_TRILINEAR"))
    patina_age = bool(values.get("F_PATINA_AGE")) and not case_hardening and style in (composite.ANTIQUED, composite.GUNSMITH)
    wanted = list(WEAPON_TEXTURES + SHARED_TEXTURES)
    if style in composite.POSITION_STYLES or ch_trilinear:
        wanted += SHAPE_TEXTURES
    elif patina_age:
        wanted.append("g_tPosition")
    if style in composite.PATTERN_STYLES or case_hardening:
        wanted.append("g_tPattern")
    if case_hardening:
        wanted.append("g_tCaseHardeningColorRamp")
    if patina_age:
        wanted += ["g_tPatinaAgeColorRamp", "g_tPatinaAgeMask"]
    if values.get("F_OVERLAY_TEXTURE"):
        wanted.append("g_tOverlay")
        if int(values.get("F_OVERLAY_MASK", 0)) == 8:
            wanted.append("g_tOverlayMask")
    if values.get("F_ROUGHNESS_TEXTURE"):
        wanted.append("g_tPaintRoughness")
    if values.get("F_METALNESS_TEXTURE"):
        wanted.append("g_tPaintMetalness")
    return wanted


def _texture_jobs(values: dict) -> dict[str, str]:
    """Shader values to convert for a finish, by the name the compositor reads them as."""
    jobs = {name: name for name in _wanted_textures(values) if values.get(name)}
    style = int(values.get("F_PAINT_STYLE", 0))
    case_hardening = bool(values.get("F_CASE_HARDENING")) and style in (composite.SOLID, composite.ANTIQUED, composite.GUNSMITH)
    patina_age = bool(values.get("F_PATINA_AGE")) and not case_hardening and style in (composite.ANTIQUED, composite.GUNSMITH)
    if (case_hardening or patina_age) and values.get("g_tInputNormal"):
        jobs["g_tNormal"] = "g_tInputNormal"
    return jobs


def _export_textures(session: Session, paths: list[str]) -> dict[str, Path]:
    """Convert a finish's textures a few at a time; returns each shader value's file."""
    unique = list(dict.fromkeys(str(path) for path in paths))
    with ThreadPoolExecutor(max_workers=EXPORT_WORKERS, thread_name_prefix="cs2rig-texture") as pool:
        return dict(zip(unique, pool.map(session.texture, unique)))


def _compose_values(kit: skins_mod.PaintKit, values: dict, textures: dict, wear: float, out_dir: Path, normal: Path | None) -> dict:
    """Compose and save a finish's images from texture pixels already read. Needs no Blender."""
    wanted = _wanted_textures(values)
    required = ("g_tWear", "g_tGrunge", "g_tPattern", "g_tCaseHardeningColorRamp", "g_tOverlay")
    missing = [name for name in required if name in wanted and name not in textures]
    if missing:
        raise SetupError(f"{kit.label} is missing a texture it needs ({', '.join(missing)}).")
    params = {name: value for name, value in values.items() if not isinstance(value, str)}
    style = int(values.get("F_PAINT_STYLE", 0))
    result = composite.compose(style, params, textures, TEXTURE_SIZE, wear, bool(values.get("F_SEPARATE_CHANNEL_INPUTS")))

    out_dir.mkdir(parents=True, exist_ok=True)
    png.write(out_dir / "colour.png", result["colour"])
    # Occlusion, roughness and metalness packed the way the glTF importer wires them.
    surface = np.stack((np.ones_like(result["roughness"]), result["roughness"], result["metalness"]), axis=-1)
    png.write(out_dir / "surface.png", surface)
    files = {"colour": out_dir / "colour.png", "surface": out_dir / "surface.png"}
    if normal is not None:
        files["normal"] = normal
    # Written last: a finish counts as made once all of its files are there.
    (out_dir / "normal.txt").write_text(str(files.get("normal", "")), encoding="utf-8")
    return files


def _compose(session: Session, kit: skins_mod.PaintKit, values: dict, wear: float, out_dir: Path) -> dict:
    """Convert a finish's textures, compose it and save its images."""
    jobs = _texture_jobs(values)
    normal_path = values.get("g_tNormal")
    exported = _export_textures(session, [values[shader] for shader in jobs.values()] + ([normal_path] if normal_path else []))
    textures = {name: _pixels(exported[values[shader]]) for name, shader in jobs.items()}
    return _compose_values(kit, values, textures, wear, out_dir, exported[str(normal_path)] if normal_path else None)


def finish_textures(session: Session, kit: skins_mod.PaintKit, body_material: str, wear: float, seed: int) -> tuple[dict, list[str]]:
    """Image files for a finish on one weapon body, composed once and then reused.

    A cached finish is answered from disk before any material values are resolved,
    so it needs no converter run. Also returns the parts of the finish that are
    not shown, in words.
    """
    wear = round(wear, 3)
    out_dir = session.cache.finish_dir(_finish_key(kit, body_material, wear, seed))
    files = _cached_finish(out_dir)
    if files is not None:
        return files, []
    values = session.finish_values(kit, body_material, seed, wear)
    missing = finish_mod.not_shown(values)
    return _compose(session, kit, values, wear, out_dir), missing


def _base_material(material: bpy.types.Material | None) -> bpy.types.Material | None:
    """The stock material a painted one stands in for, or None."""
    if material is None or BASE_MATERIAL_PROP not in material.keys():
        return None
    base = material[BASE_MATERIAL_PROP]
    if isinstance(base, str):  # files saved before the reference was kept
        base = bpy.data.materials.get(base)
    return base if isinstance(base, bpy.types.Material) and base != material else None


def set_images(material: bpy.types.Material, files: dict) -> None:
    """Point a material's colour, surface and (when given) normal textures at the finish's images."""
    tree = material.node_tree
    shader = _principled(material)
    colour = _image_node(material, "Base Color")
    colour.image = _load(files["colour"], colour=True)
    surface = _image_node(material, "Roughness")
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
        normal = _image_node(material, "Normal")
        if normal is not None:
            normal.image = _load(files["normal"], colour=False)


def worn_skin(armature: bpy.types.Object) -> str:
    """The name of the kit on the held weapon, or ''."""
    return next((obj[SKIN_PROP] for obj in armature.children_recursive if SKIN_PROP in obj.keys()), "")


def worn_skin_settings(armature: bpy.types.Object) -> tuple[str, float, int] | None:
    """The finish on the held weapon with its wear and seed, or None."""
    root = next((obj for obj in armature.children_recursive if SKIN_PROP in obj.keys()), None)
    if root is None:
        return None
    return root[SKIN_PROP], float(root.get(WEAR_PROP, 0.0)), int(root.get(SEED_PROP, 0))


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
            base = _base_material(painted)
            if base is None:
                # Saved by an older version, which let the stock material be dropped on save.
                # Keep the painted one: a new finish can still go on it.
                continue
            slot.material = base
            if painted.users == 0:
                bpy.data.materials.remove(painted)
    legacy, current = _bodies(armature)
    if current is not None:
        _show_only(current, legacy)


def _paint_slot(session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit) -> tuple[bpy.types.Object, int, str]:
    """The weapon body that takes this finish, the slot to paint and its game material."""
    legacy, current = _bodies(armature)
    body = legacy if skins_mod.uses_legacy_model(kit) and legacy is not None else current or legacy
    if body is None:
        raise RuntimeError("The agent holds no weapon to paint.")
    for index, candidate in enumerate(body.material_slots):
        source = _game_material(candidate.material)
        # A body also carries small helper materials; the one that takes paint names its paint inputs.
        if source and _image_node(candidate.material, "Base Color") is not None and session.material(source).get("composite_inputs"):
            return body, index, source
    raise RuntimeError(f"{body.name} has no material a finish can go on.")


class FinishJob:
    """Gets a finish ready, with the slow parts on a worker thread.

    Resolving the shader values, converting the game's textures and composing and
    saving the images happen off the main thread, so Blender stays usable. The two
    short steps that need Blender, reading the game's textures and putting the
    finish on the weapon, run in `step`, which the main thread calls until it
    returns True. A finish already in the cache is put on in a single step.
    """

    def __init__(self, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0):
        self.session, self.kit, self.seed = session, kit, seed
        self.wear = min(max(wear, kit.wear_min), kit.wear_max)  # what the scene keeps
        self.paint_wear = round(self.wear, 3)  # what the images are made with, and the cache key
        self.armature_name = armature.name
        body, self.slot_index, self.body_material = _paint_slot(session, armature, kit)
        self.body_name = body.name
        self.out_dir = session.cache.finish_dir(_finish_key(kit, self.body_material, self.paint_wear, seed))
        self.missing: list[str] = []
        self.stage = "Converting the game's files"
        self.cancelled = False
        self._phase = "prepare"
        self._error: BaseException | None = None
        self._tracker = proc.Tracker()
        self._thread: threading.Thread | None = None
        self._values: dict = {}
        self._paths: dict[str, Path] = {}
        self._textures: dict = {}
        self._normal: Path | None = None
        self._files = _cached_finish(self.out_dir)
        if self._files is not None:
            self._phase, self.stage = "apply", "Applying the finish"
        else:
            self._run(self._prepare)

    def _run(self, work) -> None:
        def guarded():
            try:
                with proc.tracked(self._tracker):
                    work()
            except BaseException as exc:  # raised again on the main thread
                self._error = exc
        self._thread = threading.Thread(target=guarded, name="cs2rig-finish", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Stop now: kill the converter and drop a finish that was being made."""
        self.cancelled = True
        self._tracker.cancel()
        if self._files is None:
            shutil.rmtree(self.out_dir, ignore_errors=True)

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def _prepare(self) -> None:
        values = self.session.finish_values(self.kit, self.body_material, self.seed, self.paint_wear)
        self.missing = finish_mod.not_shown(values)
        jobs = _texture_jobs(values)
        normal_path = values.get("g_tNormal")
        exported = _export_textures(self.session, [values[shader] for shader in jobs.values()] + ([normal_path] if normal_path else []))
        self._values = values
        self._paths = {name: exported[values[shader]] for name, shader in jobs.items()}
        self._normal = exported[str(normal_path)] if normal_path else None

    def _read_textures(self) -> None:
        self._textures = {name: _pixels(path) for name, path in self._paths.items()}
        self._paths = {}

    def _compose(self) -> None:
        self._files = _compose_values(self.kit, self._values, self._textures, self.paint_wear, self.out_dir, self._normal)
        self._textures = {}

    def _apply(self) -> None:
        armature = bpy.data.objects.get(self.armature_name)
        if armature is None or armature.type != "ARMATURE":
            raise RuntimeError("The agent was removed before its finish was ready.")
        body = bpy.data.objects.get(self.body_name)
        if body is None or body.type != "MESH":
            raise RuntimeError("The weapon was removed before its finish was ready.")
        remove_skin(armature)
        if self.slot_index >= len(body.material_slots) or body.material_slots[self.slot_index].material is None:
            raise RuntimeError(f"{body.name} has no material a finish can go on.")
        slot = body.material_slots[self.slot_index]
        base = _base_material(slot.material) or slot.material
        painted = slot.material.copy()
        painted.name = f"{base.name}.{self.kit.name}"
        # A reference rather than a name: it keeps the stock material in the file when it is saved.
        painted[BASE_MATERIAL_PROP] = base
        slot.material = painted
        set_images(painted, self._files)

        legacy, current = _bodies(armature)
        _show_only(body, legacy if body is current else current)
        root = next((obj for obj in importer.held_weapon_objects(armature) if importer.WEAPON_KEY_PROP in obj.keys()), body)
        root[SKIN_PROP] = self.kit.name
        root[WEAR_PROP] = self.wear
        root[SEED_PROP] = self.seed

    def step(self, context: bpy.types.Context) -> bool:
        """Move the job on from the main thread. True once the finish is on the weapon."""
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
        self._apply()
        self._phase = "done"
        return True


def apply_skin(context: bpy.types.Context, session: Session, armature: bpy.types.Object, kit: skins_mod.PaintKit, wear: float = 0.0, seed: int = 0) -> list[str]:
    """Paint the held weapon, waiting for the finish to be ready. Returns the parts that are not shown, in words."""
    job = FinishJob(session, armature, kit, wear, seed)
    while not job.step(context):
        job.wait()
    return job.missing
