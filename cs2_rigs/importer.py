"""Bring converted agents and clips into the Blender scene."""

from __future__ import annotations

import math
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Matrix

from . import eyes
from .core import catalog as catalog_mod
from .core import clipdata
from .core import glb as glb_mod
from .core import targets
from .core.catalog import Agent, Weapon
from .core.targets import (
    AGENT_KEY_PROP,
    AGENT_ROLE,
    BODY_KIND_PROP,
    CLIP_KEY_PROP,
    COMPOSED_PROP,
    DEFAULT_GLOVES_ROLE,
    FIRSTPERSON_ROOT_ROLE,
    GLOVE_ROLE,
    INSTANCE_PROP,
    POV_CAMERA_ROLE,
    ROLE_PROP,
    SLEEVES_ROLE,
    VIEWMODEL_ARMS_ROLE,
    WEAPON_BODY_ROLE,
    WEAPON_KEY_PROP,
    WEAPON_ROOT_ROLE,
    held_weapon_key,
    held_weapon_objects,
    held_weapon_root,
    is_agent,
    is_character_rig,
    is_firstperson_root,
    is_pov_camera,
    is_standalone_weapon,
    is_viewmodel_arms,
    is_weapon_root,
    weapon_key,
    weapon_objects,
    weapon_root,
)
from .session import WEAPON_RIG_CLIP, Session

DEFAULT_GLOVES_PART = "thirdperson_default_gloves"  # the agent's own mesh for its hands
WEAPON_BONE = "wpn"
WEAPON_ROOT_BONE = "weapon"  # the root bone of weapon models
GAME_FPS = 30
# Mesh parts that only make sense in first person, or are optional props.
HIDDEN_PART_MARKERS = ("firstperson", "defusekit")


def require_visible(armature: bpy.types.Object) -> None:
    """Steps that edit the skeleton need it shown: Blender cannot enter Edit or Pose Mode on a hidden object."""
    if not armature.visible_get():
        raise RuntimeError(f"{armature.name} is hidden. Unhide it first.")


def _make_active(context: bpy.types.Context, obj: bpy.types.Object) -> None:
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


@contextmanager
def working_on(context: bpy.types.Context, armature: bpy.types.Object):
    """Leave the agent active, and back in Pose Mode if it was, even when the work fails.

    Importing switches to Object Mode and makes the imported objects active; without
    this an error part-way would leave nothing selected and every button disabled.
    """
    if armature.name not in context.view_layer.objects:
        raise RuntimeError(f"{armature.name} is not in this view layer. Link it to a collection in the scene first.")
    was_posing = context.mode == "POSE" and context.active_object == armature
    # A hidden skeleton cannot be made active or posed; show it for the step and hide it again after.
    hidden, hidden_viewport = armature.hide_get(), armature.hide_viewport
    armature.hide_viewport = False
    armature.hide_set(False)
    if not armature.visible_get():
        armature.hide_set(hidden)
        armature.hide_viewport = hidden_viewport
        raise RuntimeError(f"{armature.name} cannot be shown. Make its collection visible first.")
    try:
        yield
    finally:
        if context.mode != "OBJECT" and context.active_object is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
        _make_active(context, armature)
        if was_posing:
            bpy.ops.object.mode_set(mode="POSE")
        if hidden or hidden_viewport:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            armature.hide_set(hidden)
            armature.hide_viewport = hidden_viewport


def _import_glb(context: bpy.types.Context, path: Path) -> list[bpy.types.Object]:
    # The importer only works from Object Mode.
    if context.mode != "OBJECT" and context.active_object is not None:
        bpy.ops.object.mode_set(mode="OBJECT")
    before = set(bpy.data.objects)
    for obj in context.selected_objects:
        obj.select_set(False)
    # Without disable_bone_shape the importer adds a large icosphere object for bone display.
    bpy.ops.import_scene.gltf(filepath=str(path), import_shading="NORMALS", disable_bone_shape=True)
    return [obj for obj in bpy.data.objects if obj not in before]


def _assign_action(armature: bpy.types.Object, action: bpy.types.Action) -> None:
    anim = armature.animation_data or armature.animation_data_create()
    anim.action = action
    # Blender 4.4+ actions are slotted; pick the slot the importer made.
    slots = getattr(action, "slots", None)
    if slots and getattr(anim, "action_slot", None) is None:
        anim.action_slot = slots[0]


def _fit_frame_range(context: bpy.types.Context, action: bpy.types.Action) -> None:
    start, end = action.frame_range
    context.scene.frame_start = math.floor(start)
    context.scene.frame_end = max(math.ceil(end), math.floor(start) + 1)


def _part_name(obj_name: str) -> str:
    """The mesh part an imported object holds: 'ctm_sas.thirdperson_body' -> 'thirdperson_body'.

    Older converter builds wrote 'agents\\models\\ctm_sas\\ctm_sas.vmdl_c.thirdperson_body',
    and Blender adds '.001' to a name that is already taken.
    """
    model, _, part = obj_name.rsplit(".vmdl_c.", 1)[-1].partition(".")
    part = part.split(".")[0]
    return part if part and not part.isdigit() else model

def ensure_model_export(session: Session, agent: Agent) -> Path:
    return session.export_model(agent.model_path, session.cache.model_dir(agent.key), [catalog_mod.BASE_CLIP], materials=True)

def import_agent(context: bpy.types.Context, session: Session, agent: Agent) -> bpy.types.Object:
    glb = ensure_model_export(session, agent)
    # Game clips are authored at 30 fps and land on fractional frames at Blender's
    # default 24. Only a scene still on that default is changed.
    render = context.scene.render
    if render.fps == 24 and render.fps_base == 1.0:
        render.fps = GAME_FPS
    objects = _import_glb(context, glb)
    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        bpy.data.batch_remove(objects)  # do not leave a half-imported agent in the scene
        raise RuntimeError(f"Expected one skeleton in {glb.name}, found {len(armatures)}.")
    armature = armatures[0]

    collection = bpy.data.collections.new(agent.label)
    context.scene.collection.children.link(collection)
    for obj in objects:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)
        if obj.type == "MESH":
            part = _part_name(obj.name)
            obj.name = f"{agent.label}.{part}"
            if DEFAULT_GLOVES_PART in part:
                obj[ROLE_PROP] = DEFAULT_GLOVES_ROLE
            if any(marker in part for marker in HIDDEN_PART_MARKERS):
                obj.hide_set(True)
                obj.hide_render = True

    armature.name = f"{agent.label}.skeleton"
    armature[AGENT_KEY_PROP] = agent.key
    armature[ROLE_PROP] = AGENT_ROLE
    armature[INSTANCE_PROP] = uuid.uuid4().hex
    # The export carries one base clip only so the skeleton exists; start in rest pose.
    if armature.animation_data and armature.animation_data.action:
        base = armature.animation_data.action
        armature.animation_data.action = None
        if base.users == 0:
            bpy.data.actions.remove(base)
    for bone in armature.pose.bones:
        bone.matrix_basis.identity()

    eyes.rebuild_agent_eyes(session, armature)

    _make_active(context, armature)

    return armature


def _game_rest_matrix(glb_path: Path, node: str) -> Matrix:
    """A node's rest matrix in Blender's Z-up space, as the game defines it."""
    game = Matrix(glb_mod.node_world_matrix(glb_mod.read_json(glb_path), node))
    to_blender = Matrix.Rotation(math.pi / 2, 4, "X")  # glTF is Y up
    return to_blender @ game @ to_blender.inverted()


def instance_id(obj: bpy.types.Object) -> str:
    """An imported object's id, made unique first: Shift+D copies it to the duplicate."""
    value = obj.get(INSTANCE_PROP, "")
    if not value or any(other is not obj and other.get(INSTANCE_PROP) == value for other in bpy.data.objects):
        value = uuid.uuid4().hex
        obj[INSTANCE_PROP] = value
    return value


def find_instance(instance: str) -> bpy.types.Object | None:
    """An imported object by the id stamped on it, whatever it has been renamed to."""
    return next((obj for obj in bpy.data.objects if obj.get(INSTANCE_PROP) == instance), None)


def remove_weapon(target: bpy.types.Object) -> None:
    """Remove an agent's held weapon, or delete a standalone weapon and its empty collection."""
    root = weapon_root(target)
    if root is None:
        return
    key = root.get(WEAPON_KEY_PROP, "")
    collections = list(root.users_collection) if is_standalone_weapon(root) else []
    for obj in [root, *root.children_recursive]:
        bpy.data.objects.remove(obj, do_unlink=True)
    scene_roots = {scene.collection for scene in bpy.data.scenes}
    for collection in collections:
        if collection not in scene_roots and not collection.objects and not collection.children:
            bpy.data.collections.remove(collection)
    # Clip actions made for this weapon are of no use to anything else.
    for action in [a for a in bpy.data.actions if key and a.get(WEAPON_KEY_PROP) == key and a.users == 0]:
        bpy.data.actions.remove(action)


def _export_weapon_glb(session: Session, weapon: Weapon) -> Path:
    # The clip list keeps the export small; a weapon without this clip still gets its skeleton.
    return session.export_model(weapon.model_path, session.cache.model_dir(f"weapon/{weapon.key}"), [WEAPON_RIG_CLIP], materials=True)


def _import_weapon_glb(context: bpy.types.Context, glb: Path) -> tuple[list[bpy.types.Object], bpy.types.Object]:
    actions_before = set(bpy.data.actions)
    objects = _import_glb(context, glb)
    for action in [a for a in bpy.data.actions if a not in actions_before]:
        bpy.data.actions.remove(action)
    roots = [obj for obj in objects if obj.parent is None]
    if len(roots) != 1:
        bpy.data.batch_remove(objects)
        raise RuntimeError(f"Expected one root object in {glb.name}, found {len(roots)}.")
    root = roots[0]
    for pose_bone in getattr(root.pose, "bones", []):
        pose_bone.matrix_basis.identity()
    return objects, root


def _setup_weapon_objects(
    objects: list[bpy.types.Object], root: bpy.types.Object, weapon: Weapon, collection: bpy.types.Collection
) -> None:
    meshes = [obj for obj in objects if obj.type == "MESH"]
    has_hd = any("body_hd" in obj.name for obj in meshes)
    for obj in objects:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)
        if obj.type == "MESH":
            part = _part_name(obj.name)
            obj.name = f"{weapon.key}.{part}"
            obj[ROLE_PROP] = WEAPON_BODY_ROLE
            obj[BODY_KIND_PROP] = targets.weapon_body_kind(part)
            # Weapons ship an older low-detail body next to the current one.
            if has_hd and "legacy" in part:
                obj.hide_set(True)
                obj.hide_render = True
    root.name = f"{weapon.key}.weapon"
    root[WEAPON_KEY_PROP] = weapon.key
    root[ROLE_PROP] = WEAPON_ROOT_ROLE
    root[INSTANCE_PROP] = uuid.uuid4().hex


def import_weapon(context: bpy.types.Context, session: Session, weapon: Weapon) -> bpy.types.Object:
    """Import a weapon or knife into the scene on its own, without an agent."""
    glb = _export_weapon_glb(session, weapon)
    objects, root = _import_weapon_glb(context, glb)
    collection = bpy.data.collections.new(weapon.name or weapon.label)
    context.scene.collection.children.link(collection)
    _setup_weapon_objects(objects, root, weapon, collection)
    _make_active(context, root)
    for obj in objects:
        obj.select_set(True)
    return root


def attach_weapon(context: bpy.types.Context, session: Session, armature: bpy.types.Object, weapon: Weapon) -> bpy.types.Object:
    """Put a weapon on the rig's weapon bone, where third-person or viewmodel clips expect it."""
    if WEAPON_BONE not in armature.data.bones:
        raise RuntimeError(f"This skeleton has no '{WEAPON_BONE}' bone to hold a weapon.")
    if is_viewmodel_arms(armature):
        bone_in_game = _game_rest_matrix(session.fp_model_export(), WEAPON_BONE)
    else:
        agent = session.agent(armature[AGENT_KEY_PROP])
        bone_in_game = _game_rest_matrix(ensure_model_export(session, agent), WEAPON_BONE)
    glb = _export_weapon_glb(session, weapon)

    # The game lines the weapon's own root bone up with the agent's weapon bone,
    # not the weapon model's origin.
    try:
        weapon_root_in_game = _game_rest_matrix(glb, glb_mod.root_joint_name(glb_mod.read_json(glb), WEAPON_ROOT_BONE))
    except ValueError as exc:
        raise RuntimeError(f"{weapon.label} cannot be held: {exc}") from exc

    objects, root = _import_weapon_glb(context, glb)

    remove_weapon(armature)
    collection = (armature.users_collection or [context.scene.collection])[0]
    _setup_weapon_objects(objects, root, weapon, collection)

    bone = armature.data.bones[WEAPON_BONE]
    root.parent = armature
    root.parent_type = "BONE"
    root.parent_bone = WEAPON_BONE
    # Bone children hang off the bone's tail; undo that, then go from the bone's
    # Blender rest frame to the frame the game gives it.
    root.matrix_parent_inverse = (
        Matrix.Translation((0.0, -bone.length, 0.0))
        @ bone.matrix_local.inverted()
        @ bone_in_game
        @ weapon_root_in_game.inverted()
    )
    root.matrix_basis.identity()

    load_weapon_clip(context, session, armature)

    _make_active(context, armature)

    return root


def find_agent_armature(context: bpy.types.Context) -> bpy.types.Object | None:
    """The third-person agent the panel acts on: the active one, else a selected one, else the only one."""
    return targets.find_agent_armature(context.active_object, context.selected_objects, context.scene.objects)


def find_viewmodel_arms(context: bpy.types.Context) -> bpy.types.Object | None:
    """The first-person arms rig the panel acts on: the active one, else a selected one, else the only one."""
    return targets.find_viewmodel_arms(context.active_object, context.selected_objects, context.scene.objects)


def find_character_rig(context: bpy.types.Context) -> bpy.types.Object | None:
    """The third-person agent or first-person arms the panel acts on."""
    return targets.find_character_rig(context.active_object, context.selected_objects, context.scene.objects)


def find_standalone_weapon(context: bpy.types.Context) -> bpy.types.Object | None:
    """The standalone weapon the panel acts on: the active one, else a selected one, else the only one."""
    return targets.find_standalone_weapon(context.active_object, context.selected_objects, context.scene.objects)


def find_weapon_target(context: bpy.types.Context) -> bpy.types.Object | None:
    """What the Weapon section acts on: a character rig (for its held weapon) or a standalone weapon."""
    return targets.find_weapon_target(context.active_object, context.selected_objects, context.scene.objects)


def import_clip_action(context: bpy.types.Context, rig_glb: Path, clip_glb: Path, clip: str, work_dir: Path | None = None) -> bpy.types.Action:
    """Put a converted clip onto an agent's skeleton file, import it and keep only the action.

    The clip's bone tracks are copied into the agent's own export first, so Blender
    builds the action against the same rest pose as the agent in the scene.
    """
    action = _import_grafted(context, rig_glb, clip_glb, clip, work_dir)
    if action is None:
        raise RuntimeError(f"The converter wrote no animation for '{catalog_mod.clip_label(clip)}'.")
    return action


def _import_grafted(context, rig_glb: Path, clip_glb: Path, clip: str, work_dir: Path | None = None, below: str = "") -> bpy.types.Action | None:
    """The clip's tracks put onto `rig_glb` and imported as an action; None when no bone matched."""
    label = catalog_mod.clip_label(clip)
    grafted = (work_dir or rig_glb.parent) / f"clip-{os.getpid()}.glb"
    try:
        if not glb_mod.graft_animation(rig_glb, clip_glb, grafted, label, below):
            return None
        actions_before = set(bpy.data.actions)
        objects = _import_glb(context, grafted)
    finally:
        grafted.unlink(missing_ok=True)
    new_actions = [a for a in bpy.data.actions if a not in actions_before]
    meshes = {obj.data for obj in objects if obj.type == "MESH"}
    armature_datas = {obj.data for obj in objects if obj.type == "ARMATURE"}
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for data in armature_datas:
        if data.users == 0:
            bpy.data.armatures.remove(data)
    if not new_actions:
        return None
    action = max(new_actions, key=lambda a: a.frame_range[1] - a.frame_range[0])
    for other in new_actions:
        if other is not action:
            bpy.data.actions.remove(other)
    action.name = label
    action[CLIP_KEY_PROP] = clip
    return action


def load_clip(context: bpy.types.Context, session: Session, armature: bpy.types.Object, clip: str) -> bpy.types.Action:
    """Convert one game clip for this third-person agent or first-person arms rig and make it its action."""
    label = catalog_mod.clip_label(clip)
    fp = is_viewmodel_arms(armature)
    # Reuse a clip already loaded for this rig type / agent. Names are not reliable for this:
    # two rigs with the same clip get "name" and "name.001".
    for existing in bpy.data.actions:
        if existing.get(CLIP_KEY_PROP) != clip:
            continue
        if fp and existing.get(ROLE_PROP) == VIEWMODEL_ARMS_ROLE:
            _assign_action(armature, existing)
            _fit_frame_range(context, existing)
            load_weapon_clip(context, session, armature)
            return existing
        if not fp and existing.get(ROLE_PROP) != VIEWMODEL_ARMS_ROLE and existing.get(AGENT_KEY_PROP) == armature[AGENT_KEY_PROP]:
            _assign_action(armature, existing)
            _fit_frame_range(context, existing)
            load_weapon_clip(context, session, armature)
            return existing

    info = session.clip_info(clip)
    expected_skeleton = clipdata.VIEWMODEL if fp else clipdata.WORLDMODEL
    if info.get("skeleton") != expected_skeleton:
        skeleton = info.get("skeleton_path", "").rsplit("/", 1)[-1] or "another skeleton"
        if fp:
            raise RuntimeError(f"'{label}' is made for {skeleton}, not the first-person arms skeleton.")
        raise RuntimeError(f"'{label}' is made for {skeleton}, not the agent skeleton, so it cannot play on an agent.")

    clip_glb, composed = session.export_clip(clip)
    if fp:
        rig_glb = session.fp_rig_export()
    else:
        agent_key = armature[AGENT_KEY_PROP]
        rig_glb = session.rig_export(session.agent(agent_key))
    action = import_clip_action(context, rig_glb, clip_glb, clip)
    if fp:
        action[ROLE_PROP] = VIEWMODEL_ARMS_ROLE
    else:
        action[AGENT_KEY_PROP] = armature[AGENT_KEY_PROP]
    if composed:
        action[COMPOSED_PROP] = True
    action.use_fake_user = True
    _assign_action(armature, action)

    _fit_frame_range(context, action)

    load_weapon_clip(context, session, armature)

    _make_active(context, armature)

    return action


def _weapon_clip_action(context: bpy.types.Context, session: Session, weapon: Weapon, clip: str) -> bpy.types.Action | None:
    """The clip's tracks for the weapon's own bones as an action, or None when it has none for this weapon."""
    for existing in bpy.data.actions:
        if existing.get(CLIP_KEY_PROP) == clip and existing.get(WEAPON_KEY_PROP) == weapon.key:
            return existing
    info = session.clip_info(clip)
    if not any(catalog_mod.weapon_skeleton_fits(weapon.key, skeleton) for skeleton in info.get("secondary", [])):
        return None
    rig = session.weapon_rig_export(weapon)
    try:
        root = glb_mod.root_joint_name(glb_mod.read_json(rig), WEAPON_ROOT_BONE)
    except ValueError:
        return None
    clip_glb, _composed = session.export_clip(clip)
    action = _import_grafted(context, rig, clip_glb, clip, below=root)
    if action is not None:
        action.name = f"{catalog_mod.clip_label(clip)} ({weapon.key})"
        action[WEAPON_KEY_PROP] = weapon.key
    return action


def load_weapon_clip(context: bpy.types.Context, session: Session, armature: bpy.types.Object) -> bpy.types.Action | None:
    """Play the agent's clip on the held weapon's own bones: slides, bolts, magazines, knife blades.

    The weapon's previous clip comes off first, so a clip with no tracks for this
    weapon leaves it in its rest pose rather than in the last clip's.
    """
    root = held_weapon_root(armature)
    if root is None or root.type != "ARMATURE":
        return None
    if root.animation_data:
        root.animation_data.action = None
    for bone in root.pose.bones:
        bone.matrix_basis.identity()
    agent_action = armature.animation_data.action if armature.animation_data else None
    clip = agent_action.get(CLIP_KEY_PROP) if agent_action else None
    weapon = next((w for w in catalog_mod.catalog_weapons(session.catalog()) if w.key == root[WEAPON_KEY_PROP]), None)
    if not clip or weapon is None:
        return None
    action = _weapon_clip_action(context, session, weapon, clip)
    if action is not None:
        # A weapon track may hold one key for the whole clip; keep the clip's length.
        action.use_frame_range = True
        action.frame_start, action.frame_end = agent_action.frame_range
        _assign_action(root, action)
    return action
