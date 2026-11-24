"""Bring converted agents and clips into the Blender scene."""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Matrix

from . import eyes
from .core import catalog as catalog_mod
from .core import clipdata
from .core import glb as glb_mod
from .core.catalog import Agent, Weapon
from .session import Session

AGENT_KEY_PROP = "cs2rig_agent"
WEAPON_KEY_PROP = "cs2rig_weapon"
CLIP_KEY_PROP = "cs2rig_clip"
COMPOSED_PROP = "cs2rig_composed"  # an additive clip shown over the bind pose
WEAPON_BONE = "wpn"
WEAPON_ROOT_BONE = "weapon"  # the root bone of weapon models
GAME_FPS = 30
# Mesh parts that only make sense in first person, or are optional props.
HIDDEN_PART_MARKERS = ("firstperson", "defusekit")


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
    was_posing = context.mode == "POSE" and context.active_object == armature
    try:
        yield
    finally:
        if context.mode != "OBJECT" and context.active_object is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
        _make_active(context, armature)
        if was_posing:
            bpy.ops.object.mode_set(mode="POSE")


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
            if any(marker in part for marker in HIDDEN_PART_MARKERS):
                obj.hide_set(True)
                obj.hide_render = True

    armature.name = f"{agent.label}.skeleton"
    armature[AGENT_KEY_PROP] = agent.key
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


def held_weapon_objects(armature: bpy.types.Object) -> list[bpy.types.Object]:
    return [obj for obj in armature.children_recursive if obj.get(WEAPON_KEY_PROP) or (obj.parent and obj.parent.get(WEAPON_KEY_PROP))]


def held_weapon_key(armature: bpy.types.Object) -> str:
    """The catalogue key of the weapon the agent holds, or '' when its hands are empty."""
    return next((obj[WEAPON_KEY_PROP] for obj in armature.children_recursive if WEAPON_KEY_PROP in obj.keys()), "")


def remove_weapon(armature: bpy.types.Object) -> None:
    for obj in held_weapon_objects(armature):
        bpy.data.objects.remove(obj, do_unlink=True)


def attach_weapon(context: bpy.types.Context, session: Session, armature: bpy.types.Object, weapon: Weapon) -> bpy.types.Object:
    """Put a weapon on the agent's weapon bone, where third-person clips expect it."""
    if WEAPON_BONE not in armature.data.bones:
        raise RuntimeError(f"This agent has no '{WEAPON_BONE}' bone to hold a weapon.")
    agent = session.agent(armature[AGENT_KEY_PROP])
    bone_in_game = _game_rest_matrix(ensure_model_export(session, agent), WEAPON_BONE)
    # The clip list keeps the export small; a weapon without this clip still gets its skeleton.
    glb = session.export_model(weapon.model_path, session.cache.model_dir(f"weapon/{weapon.key}"), ["dropped"], materials=True)

    # The game lines the weapon's own root bone up with the agent's weapon bone,
    # not the weapon model's origin.
    try:
        weapon_root_in_game = _game_rest_matrix(glb, glb_mod.root_joint_name(glb_mod.read_json(glb), WEAPON_ROOT_BONE))
    except ValueError as exc:
        raise RuntimeError(f"{weapon.label} cannot be held: {exc}") from exc

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

    remove_weapon(armature)
    meshes = [obj for obj in objects if obj.type == "MESH"]
    has_hd = any("body_hd" in obj.name for obj in meshes)
    collection = armature.users_collection[0]
    for obj in objects:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)
        if obj.type == "MESH":
            part = _part_name(obj.name)
            obj.name = f"{weapon.key}.{part}"
            # Weapons ship an older low-detail body next to the current one.
            if has_hd and "legacy" in part:
                obj.hide_set(True)
                obj.hide_render = True
    root.name = f"{weapon.key}.weapon"
    root[WEAPON_KEY_PROP] = weapon.key

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

    _make_active(context, armature)

    return root


def _agent_above(obj: bpy.types.Object | None) -> bpy.types.Object | None:
    while obj is not None:
        if obj.type == "ARMATURE" and AGENT_KEY_PROP in obj.keys():
            return obj
        obj = obj.parent
    return None


def find_agent_armature(context: bpy.types.Context) -> bpy.types.Object | None:
    """The agent the panel acts on: the active one, else a selected one, else the only one."""
    agent = _agent_above(context.active_object)
    if agent is not None:
        return agent
    for obj in context.selected_objects:
        agent = _agent_above(obj)
        if agent is not None:
            return agent
    agents = [obj for obj in context.scene.objects if obj.type == "ARMATURE" and AGENT_KEY_PROP in obj.keys()]
    return agents[0] if len(agents) == 1 else None


def import_clip_action(context: bpy.types.Context, rig_glb: Path, clip_glb: Path, clip: str, work_dir: Path | None = None) -> bpy.types.Action:
    """Put a converted clip onto an agent's skeleton file, import it and keep only the action.

    The clip's bone tracks are copied into the agent's own export first, so Blender
    builds the action against the same rest pose as the agent in the scene.
    """
    label = catalog_mod.clip_label(clip)
    grafted = (work_dir or rig_glb.parent) / f"clip-{os.getpid()}.glb"
    try:
        if not glb_mod.graft_animation(rig_glb, clip_glb, grafted, label):
            raise RuntimeError(f"The converter wrote no animation for '{label}'.")
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
        raise RuntimeError(f"The converter wrote no animation for '{label}'.")
    action = max(new_actions, key=lambda a: a.frame_range[1] - a.frame_range[0])
    for other in new_actions:
        if other is not action:
            bpy.data.actions.remove(other)
    action.name = label
    action[CLIP_KEY_PROP] = clip
    return action


def load_clip(context: bpy.types.Context, session: Session, armature: bpy.types.Object, clip: str) -> bpy.types.Action:
    """Convert one game clip for this agent and make it the armature's action."""
    label = catalog_mod.clip_label(clip)
    # Reuse a clip already loaded for this agent. Names are not reliable for this:
    # two agents with the same clip get "name" and "name.001".
    for existing in bpy.data.actions:
        if existing.get(CLIP_KEY_PROP) == clip and existing.get(AGENT_KEY_PROP) == armature[AGENT_KEY_PROP]:
            _assign_action(armature, existing)
            _fit_frame_range(context, existing)
            return existing

    info = session.clip_info(clip)
    if info.get("skeleton") != clipdata.WORLDMODEL:
        skeleton = info.get("skeleton_path", "").rsplit("/", 1)[-1] or "another skeleton"
        raise RuntimeError(f"'{label}' is made for {skeleton}, not the agent skeleton, so it cannot play on an agent.")
    agent_key = armature[AGENT_KEY_PROP]
    agent = session.agent(agent_key)
    clip_glb, composed = session.export_clip(clip)
    action = import_clip_action(context, session.rig_export(agent), clip_glb, clip)
    action[AGENT_KEY_PROP] = agent_key
    if composed:
        action[COMPOSED_PROP] = True
    action.use_fake_user = True
    _assign_action(armature, action)

    _fit_frame_range(context, action)

    _make_active(context, armature)

    return action
