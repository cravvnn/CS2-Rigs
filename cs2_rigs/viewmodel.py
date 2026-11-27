"""Import first-person viewmodel arms, agent sleeves, and a calibrated Source 2 POV camera."""

from __future__ import annotations

import math
import uuid

import bmesh
import bpy
from mathutils import Euler, Vector

from . import importer, paint, rig
from .core import targets
from .core import viewmodel as vm_core
from .core.catalog import Agent
from .session import Session

POV_PRESET_PROP = "cs2rig_pov_preset"
POV_FOV_PROP = "cs2rig_pov_fov"
POV_OFFSET_X_PROP = "cs2rig_pov_offset_x"
POV_OFFSET_Y_PROP = "cs2rig_pov_offset_y"
POV_OFFSET_Z_PROP = "cs2rig_pov_offset_z"
BARE_ARM_MAT_PROP = "cs2rig_bare_arm_mat"

SLEEVES_PART = "firstperson_sleeves"
FP_DEFAULT_GLOVES_PART = "firstperson_default_gloves"


def _is_bare_arm_material(material: bpy.types.Material | None) -> bool:
    if material is None:
        return False
    game_mat = paint._game_material(material)
    return "bare_arm" in game_mat.lower() or "bare_arm" in material.name.lower()


def strip_bare_arm_faces(mesh_obj: bpy.types.Object) -> int:
    """Remove bare-arm faces from a first-person glove/arms mesh so they never poke through sleeves."""
    if mesh_obj.type != "MESH" or mesh_obj.data is None:
        return 0
    bare_slots = {idx for idx, slot in enumerate(mesh_obj.material_slots) if _is_bare_arm_material(slot.material)}
    if not bare_slots:
        return 0
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_obj.data)
        to_remove = [face for face in bm.faces if face.material_index in bare_slots]
        if not to_remove:
            return 0
        removed = len(to_remove)
        bmesh.ops.delete(bm, geom=to_remove, context="FACES")
        bm.to_mesh(mesh_obj.data)
        mesh_obj.data.update()
        return removed
    finally:
        bm.free()


def has_visible_sleeves(armature: bpy.types.Object) -> bool:
    """True when the first-person rig carries visible agent sleeves."""
    for obj in armature.children_recursive:
        if obj.type != "MESH":
            continue
        is_sleeves = obj.get(targets.ROLE_PROP) == targets.SLEEVES_ROLE or SLEEVES_PART in obj.name
        if is_sleeves and not obj.hide_get() and not obj.hide_render:
            return True
    return False


def bare_arm_material(armature: bpy.types.Object) -> bpy.types.Material | None:
    """The agent's bare-arm skin material saved on the FP rig, for matching sleeveless custom gloves."""
    stored = armature.get(BARE_ARM_MAT_PROP)
    if isinstance(stored, bpy.types.Material):
        return stored
    for obj in armature.children_recursive:
        if obj.type != "MESH":
            continue
        if obj.get(targets.ROLE_PROP) == targets.DEFAULT_GLOVES_ROLE or FP_DEFAULT_GLOVES_PART in obj.name:
            for slot in obj.material_slots:
                if _is_bare_arm_material(slot.material):
                    return slot.material
    return None


def find_pov_camera(armature: bpy.types.Object) -> bpy.types.Object | None:
    """The calibrated POV camera paired with a first-person arms armature."""
    parent = armature.parent
    if parent is not None and targets.is_firstperson_root(parent):
        cam = next((child for child in parent.children_recursive if targets.is_pov_camera(child)), None)
        if cam is not None:
            return cam
    for collection in armature.users_collection:
        cam = next((obj for obj in collection.objects if targets.is_pov_camera(obj)), None)
        if cam is not None:
            return cam
    return None


def apply_pov_settings(
    armature: bpy.types.Object,
    preset: str,
    fov: float,
    offset_x: float,
    offset_y: float,
    offset_z: float,
) -> None:
    """Apply viewmodel FOV and camera-space inch offsets to the FP rig and its POV camera."""
    if preset in ("classic", "desktop"):
        p = vm_core.get_preset(preset)
        fov = p.fov
        offset_x, offset_y, offset_z = p.offset
    else:
        preset = vm_core.detect_preset(fov, offset_x, offset_y, offset_z)

    fov = max(vm_core.FOV_MIN, min(vm_core.FOV_MAX, float(fov)))
    armature.location = Vector(vm_core.offset_inches_to_blender(offset_x, offset_y, offset_z))

    cam = find_pov_camera(armature)
    if cam is not None and cam.data is not None:
        cam.data.type = "PERSP"
        cam.data.lens_unit = "FOV"
        cam.data.sensor_fit = "VERTICAL"
        cam.data.angle = vm_core.source2_fov_to_vertical_rad(fov)
        cam.data.clip_start = vm_core.POV_CLIP_START
        cam.data.clip_end = vm_core.POV_CLIP_END

    armature[POV_PRESET_PROP] = preset
    armature[POV_FOV_PROP] = float(fov)
    armature[POV_OFFSET_X_PROP] = float(offset_x)
    armature[POV_OFFSET_Y_PROP] = float(offset_y)
    armature[POV_OFFSET_Z_PROP] = float(offset_z)


def read_pov_settings(armature: bpy.types.Object) -> tuple[str, float, float, float, float]:
    """Read (preset, fov, offset_x, offset_y, offset_z) stored on a first-person rig."""
    classic = vm_core.PRESETS["classic"]
    if POV_FOV_PROP in armature.keys():
        fov = float(armature[POV_FOV_PROP])
        ox = float(armature.get(POV_OFFSET_X_PROP, classic.offset[0]))
        oy = float(armature.get(POV_OFFSET_Y_PROP, classic.offset[1]))
        oz = float(armature.get(POV_OFFSET_Z_PROP, classic.offset[2]))
        preset = str(armature.get(POV_PRESET_PROP, vm_core.detect_preset(fov, ox, oy, oz)))
        return preset, fov, ox, oy, oz
    cam = find_pov_camera(armature)
    fov = vm_core.vertical_rad_to_source2_fov(cam.data.angle) if cam is not None and cam.data else classic.fov
    ox, oy, oz = vm_core.blender_to_offset_inches(*armature.location)
    preset = vm_core.detect_preset(fov, ox, oy, oz)
    return preset, fov, ox, oy, oz


def look_through_pov_camera(context: bpy.types.Context, armature: bpy.types.Object) -> bpy.types.Object:
    """Make the rig's POV camera the active scene camera and switch the 3D viewport to it."""
    cam = find_pov_camera(armature)
    if cam is None:
        raise RuntimeError("No POV camera was found for this first-person rig.")
    context.scene.camera = cam
    space = getattr(context, "space_data", None)
    if space is not None and space.type == "VIEW_3D" and space.region_3d is not None:
        space.region_3d.view_perspective = "CAMERA"
    return cam


def import_viewmodel(
    context: bpy.types.Context,
    session: Session,
    agent: Agent,
    preset: str = "classic",
) -> bpy.types.Object:
    """Import first-person arms with the chosen agent's sleeves/gloves and a calibrated POV camera."""
    arms_glb = session.fp_model_export()
    render = context.scene.render
    if render.fps == 24 and render.fps_base == 1.0:
        render.fps = importer.GAME_FPS

    actions_before = set(bpy.data.actions)
    objects = importer._import_glb(context, arms_glb)
    for action in [a for a in bpy.data.actions if a not in actions_before]:
        bpy.data.actions.remove(action)

    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        bpy.data.batch_remove(objects)
        raise RuntimeError(f"Expected one skeleton in {arms_glb.name}, found {len(armatures)}.")
    armature = armatures[0]
    base_meshes = [obj for obj in objects if obj.type == "MESH"]

    collection = bpy.data.collections.new(f"FirstPerson ({agent.label})")
    context.scene.collection.children.link(collection)

    # FirstPersonRoot Empty anchors POV_Camera and fp_skeleton together for dropping into any map.
    fp_root = bpy.data.objects.new(f"{agent.label}.fp_root", None)
    fp_root.empty_display_type = "ARROWS"
    fp_root.empty_display_size = 0.15
    fp_root[targets.ROLE_PROP] = targets.FIRSTPERSON_ROOT_ROLE
    fp_root[targets.INSTANCE_PROP] = uuid.uuid4().hex
    collection.objects.link(fp_root)

    for obj in objects:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        collection.objects.link(obj)

    armature.name = f"{agent.label}.fp_skeleton"
    armature.parent = fp_root
    armature.matrix_parent_inverse.identity()
    armature[targets.ROLE_PROP] = targets.VIEWMODEL_ARMS_ROLE
    armature[targets.AGENT_KEY_PROP] = agent.key
    armature[targets.INSTANCE_PROP] = uuid.uuid4().hex

    if armature.animation_data and armature.animation_data.action:
        armature.animation_data.action = None
    for bone in armature.pose.bones:
        bone.matrix_basis.identity()

    # Forearm twist constraints keep wrists and cuffs smooth during inspect/reload clips.
    rig._add_twists(armature)

    # Import agent first-person sleeves and default gloves meshes, then rebind them to fp_skeleton.
    agent_glb = importer.ensure_model_export(session, agent)
    agent_actions_before = set(bpy.data.actions)
    agent_objs = importer._import_glb(context, agent_glb)
    for action in [a for a in bpy.data.actions if a not in agent_actions_before]:
        bpy.data.actions.remove(action)

    agent_sleeves = None
    agent_fp_default = None
    agent_bare_arm_mat = None
    for obj in agent_objs:
        if obj.type != "MESH":
            continue
        part = importer._part_name(obj.name)
        if SLEEVES_PART in part:
            agent_sleeves = obj
        elif FP_DEFAULT_GLOVES_PART in part:
            agent_fp_default = obj
            for slot in obj.material_slots:
                if _is_bare_arm_material(slot.material):
                    agent_bare_arm_mat = slot.material
                    break

    keep_agent_objs = [obj for obj in (agent_sleeves, agent_fp_default) if obj is not None]
    drop_objs = [obj for obj in agent_objs if obj not in keep_agent_objs]
    drop_datas = {obj.data for obj in drop_objs if obj.data is not None}
    bpy.data.batch_remove(drop_objs)
    for data in drop_datas:
        if data.users == 0 and isinstance(data, (bpy.types.Mesh, bpy.types.Armature)):
            (bpy.data.meshes if isinstance(data, bpy.types.Mesh) else bpy.data.armatures).remove(data)

    for mesh_obj in keep_agent_objs:
        for old in list(mesh_obj.users_collection):
            old.objects.unlink(mesh_obj)
        collection.objects.link(mesh_obj)
        mesh_obj.parent = armature
        mesh_obj.matrix_parent_inverse.identity()
        for mod in mesh_obj.modifiers:
            if mod.type == "ARMATURE":
                mod.object = armature

    if agent_bare_arm_mat is not None:
        armature[BARE_ARM_MAT_PROP] = agent_bare_arm_mat

    if agent_sleeves is not None:
        agent_sleeves.name = f"{agent.label}.firstperson_sleeves"
        agent_sleeves[targets.ROLE_PROP] = targets.SLEEVES_ROLE
        agent_sleeves.hide_set(False)
        agent_sleeves.hide_render = False

    if agent_fp_default is not None:
        agent_fp_default.name = f"{agent.label}.firstperson_default_gloves"
        agent_fp_default[targets.ROLE_PROP] = targets.DEFAULT_GLOVES_ROLE
        agent_fp_default.hide_set(False)
        agent_fp_default.hide_render = False
        # Strip bare-arm faces when sleeves are visible to prevent elbow poke-through.
        if agent_sleeves is not None:
            strip_bare_arm_faces(agent_fp_default)
        for base in base_meshes:
            base.name = f"{agent.label}.weapon_arms_base"
            base.hide_set(True)
            base.hide_render = True
    else:
        for base in base_meshes:
            base.name = f"{agent.label}.firstperson_default_gloves"
            base[targets.ROLE_PROP] = targets.DEFAULT_GLOVES_ROLE
            if agent_sleeves is not None:
                strip_bare_arm_faces(base)

    # Calibrated POV Camera at FirstPersonRoot origin looking down -Y (+Z up, -X right).
    cam_data = bpy.data.cameras.new(f"{agent.label}.pov_camera")
    cam_data.type = "PERSP"
    cam_data.lens_unit = "FOV"
    cam_data.sensor_fit = "VERTICAL"
    cam_data.clip_start = vm_core.POV_CLIP_START
    cam_data.clip_end = vm_core.POV_CLIP_END
    cam_data.display_size = 0.12

    cam_obj = bpy.data.objects.new(f"{agent.label}.pov_camera", cam_data)
    collection.objects.link(cam_obj)
    cam_obj.parent = fp_root
    cam_obj.matrix_parent_inverse.identity()
    cam_obj.location = Vector((0.0, 0.0, 0.0))
    cam_obj.rotation_euler = Euler((math.pi * 0.5, 0.0, math.pi), "XYZ")
    cam_obj[targets.ROLE_PROP] = targets.POV_CAMERA_ROLE
    cam_obj[targets.INSTANCE_PROP] = uuid.uuid4().hex

    p = vm_core.get_preset(preset)
    apply_pov_settings(armature, p.key, p.fov, *p.offset)

    if context.scene.camera is None:
        context.scene.camera = cam_obj

    importer._make_active(context, armature)
    return armature
