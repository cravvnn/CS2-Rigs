"""One-click Blender studio setup: 3-point lighting, seamless backdrop, framed camera and turntable."""

from __future__ import annotations

import bpy
from mathutils import Vector

from . import hdri, importer
from .core import studio as studio_core
from .core import targets

STUDIO_COLLECTION_NAME = "CS2 Rigs Studio"
STUDIO_COLLECTION_PROP = "cs2rig_studio"
STUDIO_VERSION_PROP = "cs2rig_studio_version"
STUDIO_VERSION = 1
STUDIO_PRESET_PROP = "cs2rig_studio_preset"
STUDIO_BACKDROP_PROP = "cs2rig_studio_backdrop"
STUDIO_ROLE_PROP = "cs2rig_studio_role"
STUDIO_WORLD_PROP = "cs2rig_studio_world"
STUDIO_MAT_PROP = "cs2rig_studio_material"
STUDIO_ACTION_PROP = "cs2rig_studio_action"
PREV_CAMERA_PROP = "cs2rig_studio_prev_camera"
PREV_WORLD_PROP = "cs2rig_studio_prev_world"

STUDIO_WORLD_NAME = "CS2 Rigs Studio World"
STUDIO_MAT_NAME = "CS2 Studio Backdrop"
STUDIO_ACTION_NAME = "CS2 Studio Turntable"

ROLE_PIVOT = "pivot"
ROLE_TARGET = "target"
ROLE_CAMERA = "camera"
ROLE_BACKDROP = "backdrop"

EYE_BONES = ("eyeball_l", "eyeball_r")
HEAD_BONE = "head_0"


def find_studio_collection(scene: bpy.types.Scene) -> bpy.types.Collection | None:
    """Return the studio collection linked in `scene`, if present."""
    for collection in scene.collection.children_recursive:
        if collection.get(STUDIO_COLLECTION_PROP):
            return collection
    return None


def has_studio(scene: bpy.types.Scene) -> bool:
    return find_studio_collection(scene) is not None


def studio_preset(scene: bpy.types.Scene) -> str:
    collection = find_studio_collection(scene)
    if collection is None:
        return "studio"
    return str(collection.get(STUDIO_PRESET_PROP, "studio"))


def studio_backdrop_enabled(scene: bpy.types.Scene) -> bool:
    collection = find_studio_collection(scene)
    if collection is None:
        return True
    return bool(collection.get(STUDIO_BACKDROP_PROP, True))


def _studio_objects(scene: bpy.types.Scene) -> list[bpy.types.Object]:
    """Every object belonging to the studio in `scene`."""
    collection = find_studio_collection(scene)
    seen: set[int] = set()
    found: list[bpy.types.Object] = []
    sources = list(collection.all_objects) if collection is not None else []
    sources.extend(obj for obj in scene.objects if STUDIO_ROLE_PROP in obj.keys())
    for obj in sources:
        ptr = obj.as_pointer()
        if ptr not in seen:
            seen.add(ptr)
            found.append(obj)
    return found


def find_studio_object(scene: bpy.types.Scene, role: str) -> bpy.types.Object | None:
    return next((obj for obj in _studio_objects(scene) if obj.get(STUDIO_ROLE_PROP) == role), None)


def is_studio_object(obj: bpy.types.Object | None) -> bool:
    if obj is None:
        return False
    if STUDIO_ROLE_PROP in obj.keys():
        return True
    return any(col.get(STUDIO_COLLECTION_PROP) for col in obj.users_collection)


def _action_fcurves(action: bpy.types.Action, slot=None):
    """Return the F-curves collection for `action` across Blender 4.x and 5.x slotted actions."""
    layers = getattr(action, "layers", None)
    if layers:
        target_slot = slot
        if target_slot is None:
            slots = getattr(action, "slots", None)
            target_slot = slots[0] if slots else None
        if target_slot is not None:
            for layer in layers:
                for strip in getattr(layer, "strips", ()):
                    bag = strip.channelbag(target_slot, ensure=False)
                    if bag is not None:
                        return bag.fcurves
    return getattr(action, "fcurves", ())


def has_turntable(scene: bpy.types.Scene) -> bool:
    pivot = find_studio_object(scene, ROLE_PIVOT)
    if pivot is None or pivot.animation_data is None or pivot.animation_data.action is None:
        return False
    action = pivot.animation_data.action
    return bool(action.get(STUDIO_ACTION_PROP))


def _visible_meshes(roots: list[bpy.types.Object]) -> list[bpy.types.Object]:
    seen: set[int] = set()
    meshes: list[bpy.types.Object] = []
    for root in roots:
        for obj in (root, *root.children_recursive):
            ptr = obj.as_pointer()
            if ptr in seen or is_studio_object(obj):
                continue
            seen.add(ptr)
            if obj.type == "MESH" and not obj.hide_render and not obj.hide_viewport and not obj.hide_get():
                meshes.append(obj)
    return meshes


def _subject_selection(context: bpy.types.Context) -> tuple[list[bpy.types.Object], bpy.types.Object | None]:
    """Choose which objects to frame: active agent, else selected non-studio objects, else sole agent, else scene meshes."""
    active = context.active_object
    active_agent = targets.agent_above(active) if active is not None and not is_studio_object(active) else None
    if active_agent is not None:
        return _visible_meshes([active_agent]), active_agent

    selected = [obj for obj in context.selected_objects if not is_studio_object(obj)]
    if selected:
        selected_agent = next((targets.agent_above(obj) for obj in selected if targets.agent_above(obj) is not None), None)
        if selected_agent is not None:
            return _visible_meshes([selected_agent]), selected_agent
        meshes = _visible_meshes(selected)
        return (meshes if meshes else selected), None

    sole_agent = importer.find_agent_armature(context)
    if sole_agent is not None:
        return _visible_meshes([sole_agent]), sole_agent

    scene_meshes = [
        obj
        for obj in context.scene.objects
        if obj.type == "MESH" and not is_studio_object(obj) and not obj.hide_render and not obj.hide_viewport and not obj.hide_get()
    ]
    return scene_meshes, None


def _agent_forward(armature: bpy.types.Object) -> studio_core.Vec3:
    """Detect which way the agent faces on the XY ground plane."""
    mw = armature.matrix_world
    head = armature.pose.bones.get(HEAD_BONE)
    eye_l = armature.pose.bones.get(EYE_BONES[0])
    eye_r = armature.pose.bones.get(EYE_BONES[1])
    if head is not None and eye_l is not None and eye_r is not None:
        eyes_mid = 0.5 * ((mw @ eye_l.head) + (mw @ eye_r.head))
        head_pos = mw @ head.head
        fwd = eyes_mid - head_pos
        if fwd.xy.length > 1e-4:
            return studio_core.normalize_forward_xy((fwd.x, fwd.y, 0.0))
    local_front = mw.to_3x3() @ Vector(studio_core.DEFAULT_FORWARD)
    return studio_core.normalize_forward_xy((local_front.x, local_front.y, 0.0))


def subject_bounds_and_forward(context: bpy.types.Context) -> tuple[studio_core.Bounds, studio_core.Vec3]:
    """Evaluated world-space bounding box and front direction for the current subject."""
    objects, agent = _subject_selection(context)
    depsgraph = context.evaluated_depsgraph_get()
    depsgraph.update()
    points: list[studio_core.Vec3] = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mw = evaluated.matrix_world
        for corner in evaluated.bound_box:
            world_pt = mw @ Vector(corner)
            points.append((world_pt.x, world_pt.y, world_pt.z))
    bounds = studio_core.compute_bounds(points)
    forward = _agent_forward(agent) if agent is not None else studio_core.DEFAULT_FORWARD
    return bounds, forward


def _ensure_collection(scene: bpy.types.Scene) -> tuple[bpy.types.Collection, bool]:
    existing = find_studio_collection(scene)
    if existing is not None:
        return existing, False
    collection = bpy.data.collections.new(STUDIO_COLLECTION_NAME)
    collection[STUDIO_COLLECTION_PROP] = True
    collection[STUDIO_VERSION_PROP] = STUDIO_VERSION
    scene.collection.children.link(collection)
    return collection, True


def _link_only(collection: bpy.types.Collection, obj: bpy.types.Object) -> None:
    for other in list(obj.users_collection):
        if other != collection:
            other.objects.unlink(obj)
    if obj.name not in collection.objects:
        collection.objects.link(obj)


def _set_track_to(obj: bpy.types.Object, target: bpy.types.Object) -> None:
    existing = next((c for c in obj.constraints if c.type == "TRACK_TO" and c.name == "CS2 Studio Aim"), None)
    constraint = existing or obj.constraints.new("TRACK_TO")
    constraint.name = "CS2 Studio Aim"
    constraint.target = target
    constraint.track_axis = "TRACK_NEGATIVE_Z"
    constraint.up_axis = "UP_Y"


def _ensure_world(preset: studio_core.LightingPreset) -> bpy.types.World:
    world = next((w for w in bpy.data.worlds if w.get(STUDIO_WORLD_PROP)), None)
    if world is None:
        world = bpy.data.worlds.new(STUDIO_WORLD_NAME)
        world[STUDIO_WORLD_PROP] = True
    world.color = preset.world.color
    if world.node_tree is not None:
        bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if bg is None:
            world.node_tree.nodes.clear()
            bg = world.node_tree.nodes.new("ShaderNodeBackground")
            out = world.node_tree.nodes.new("ShaderNodeOutputWorld")
            world.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
        bg.inputs["Color"].default_value = (*preset.world.color, 1.0)
        bg.inputs["Strength"].default_value = preset.world.strength
    return world


def _ensure_backdrop_material(preset: studio_core.LightingPreset) -> bpy.types.Material:
    mat = next((m for m in bpy.data.materials if m.get(STUDIO_MAT_PROP)), None)
    if mat is None:
        mat = bpy.data.materials.new(STUDIO_MAT_NAME)
        mat[STUDIO_MAT_PROP] = True
    if mat.node_tree is not None:
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            mat.node_tree.nodes.clear()
            bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
            out = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
            mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        bsdf.inputs["Base Color"].default_value = (*preset.backdrop.color, 1.0)
        bsdf.inputs["Roughness"].default_value = preset.backdrop.roughness
        if "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = preset.backdrop.specular
    return mat


def _build_backdrop_mesh(mesh: bpy.types.Mesh, spec: studio_core.CycloramaSpec) -> None:
    vertices, faces = studio_core.cyclorama_mesh_data(spec)
    mesh.clear_geometry()
    mesh.from_pydata(vertices, [], faces)
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.update()


def _aspect_ratio(scene: bpy.types.Scene) -> float:
    render = scene.render
    width = max(1.0, render.resolution_x * render.pixel_aspect_x)
    height = max(1.0, render.resolution_y * render.pixel_aspect_y)
    return width / height


def _parent_local_position(world_pos: studio_core.Vec3, pivot_pos: studio_core.Vec3) -> tuple[float, float, float]:
    return (world_pos[0] - pivot_pos[0], world_pos[1] - pivot_pos[1], world_pos[2] - pivot_pos[2])


def _apply_framing_and_lights(
    context: bpy.types.Context,
    collection: bpy.types.Collection,
    preset: studio_core.LightingPreset,
    backdrop_on: bool,
) -> None:
    scene = context.scene
    bounds, forward = subject_bounds_and_forward(context)
    cam_plan = studio_core.frame_camera(bounds, forward, aspect_ratio=_aspect_ratio(scene))
    light_plans = studio_core.place_lights(preset, bounds, forward)
    cyc_spec = studio_core.cyclorama_spec(bounds, cam_plan.distance)

    pivot_loc = (bounds.center[0], bounds.center[1], bounds.floor_z)

    # Pivot empty (holds turntable rotation without touching the agent).
    pivot = find_studio_object(scene, ROLE_PIVOT)
    if pivot is None:
        pivot = bpy.data.objects.new("CS2 Studio Pivot", None)
        pivot.empty_display_type = "PLAIN_AXES"
        pivot.empty_display_size = 0.25
        pivot[STUDIO_ROLE_PROP] = ROLE_PIVOT
    _link_only(collection, pivot)
    pivot.location = pivot_loc
    if not has_turntable(scene):
        pivot.rotation_euler = (0.0, 0.0, 0.0)

    # Aim target empty (child of pivot so camera & lights track smoothly).
    target = find_studio_object(scene, ROLE_TARGET)
    if target is None:
        target = bpy.data.objects.new("CS2 Studio Target", None)
        target.empty_display_type = "SPHERE"
        target.empty_display_size = 0.08
        target[STUDIO_ROLE_PROP] = ROLE_TARGET
    _link_only(collection, target)
    target.parent = pivot
    target.matrix_parent_inverse.identity()
    target.location = _parent_local_position(cam_plan.target, pivot_loc)

    # Studio camera.
    camera = find_studio_object(scene, ROLE_CAMERA)
    if camera is None or camera.type != "CAMERA":
        cam_data = bpy.data.cameras.new("CS2 Studio Camera")
        camera = bpy.data.objects.new("CS2 Studio Camera", cam_data)
        camera[STUDIO_ROLE_PROP] = ROLE_CAMERA
    _link_only(collection, camera)
    camera.parent = pivot
    camera.matrix_parent_inverse.identity()
    camera.location = _parent_local_position(cam_plan.location, pivot_loc)
    camera.rotation_euler = cam_plan.rotation_euler
    camera.data.lens = cam_plan.focal_length_mm
    camera.data.sensor_width = cam_plan.sensor_width_mm
    camera.data.sensor_fit = "AUTO"
    camera.data.clip_start = cam_plan.clip_start
    camera.data.clip_end = cam_plan.clip_end
    _set_track_to(camera, target)
    scene.camera = camera

    # Three-point studio area lights (children of pivot so lighting orbits cleanly on turntable).
    for plan in light_plans:
        light_obj = find_studio_object(scene, plan.role)
        if light_obj is None or light_obj.type != "LIGHT":
            light_data = bpy.data.lights.new(plan.label, "AREA")
            light_obj = bpy.data.objects.new(plan.label, light_data)
            light_obj[STUDIO_ROLE_PROP] = plan.role
        _link_only(collection, light_obj)
        light_obj.parent = pivot
        light_obj.matrix_parent_inverse.identity()
        light_obj.location = _parent_local_position(plan.location, pivot_loc)
        light_obj.rotation_euler = plan.rotation_euler
        ldata = light_obj.data
        ldata.type = "AREA"
        ldata.shape = "RECTANGLE"
        ldata.size = plan.size
        ldata.size_y = plan.size_y
        ldata.energy = plan.energy
        ldata.color = plan.color
        if hasattr(ldata, "spread"):
            ldata.spread = plan.spread_rad
        _set_track_to(light_obj, target)

    # Seamless 360-degree studio backdrop bowl (stationary on the studio floor).
    backdrop = find_studio_object(scene, ROLE_BACKDROP)
    if backdrop is None or backdrop.type != "MESH":
        mesh = bpy.data.meshes.new("CS2 Studio Backdrop")
        backdrop = bpy.data.objects.new("CS2 Studio Backdrop", mesh)
        backdrop[STUDIO_ROLE_PROP] = ROLE_BACKDROP
    _link_only(collection, backdrop)
    _build_backdrop_mesh(backdrop.data, cyc_spec)
    backdrop.location = (cyc_spec.center_xy[0], cyc_spec.center_xy[1], cyc_spec.floor_z)
    backdrop.rotation_euler = (0.0, 0.0, 0.0)
    backdrop.scale = (1.0, 1.0, 1.0)
    mat = _ensure_backdrop_material(preset)
    if backdrop.data.materials:
        backdrop.data.materials[0] = mat
    else:
        backdrop.data.materials.append(mat)
    backdrop.hide_viewport = not backdrop_on
    backdrop.hide_render = not backdrop_on
    backdrop.hide_set(not backdrop_on)

    # A CS2 HDRI, standalone or a loaded map's own sky, keeps lighting the subject:
    # the studio's lights and backdrop complement it instead of replacing it.
    if hdri.is_cs2_sky_world(scene.world):
        return
    if backdrop_on or scene.world is None or not scene.world.get("cs2rig_map"):
        scene.world = _ensure_world(preset)


def add_or_update_studio(
    context: bpy.types.Context,
    preset_key: str = "studio",
    backdrop: bool = True,
) -> bpy.types.Collection:
    """Add the studio rig to the active scene, or update and reframe it if already present."""
    scene = context.scene
    preset = studio_core.get_preset(preset_key)
    collection, created = _ensure_collection(scene)
    if created:
        prev_cam = scene.camera
        if prev_cam is not None and not is_studio_object(prev_cam):
            collection[PREV_CAMERA_PROP] = prev_cam.name
        else:
            collection[PREV_CAMERA_PROP] = ""
        prev_world = scene.world
        if prev_world is not None and not prev_world.get(STUDIO_WORLD_PROP):
            collection[PREV_WORLD_PROP] = prev_world.name
        else:
            collection[PREV_WORLD_PROP] = ""

    collection[STUDIO_PRESET_PROP] = preset.key
    collection[STUDIO_BACKDROP_PROP] = bool(backdrop)
    _apply_framing_and_lights(context, collection, preset, bool(backdrop))
    return collection


def apply_studio_look(
    context: bpy.types.Context,
    preset_key: str,
    backdrop: bool,
) -> bool:
    """Update lighting preset, world fill and backdrop visibility on an existing studio without moving the camera."""
    scene = context.scene
    collection = find_studio_collection(scene)
    if collection is None:
        return False
    preset = studio_core.get_preset(preset_key)
    collection[STUDIO_PRESET_PROP] = preset.key
    collection[STUDIO_BACKDROP_PROP] = bool(backdrop)

    bounds, forward = subject_bounds_and_forward(context)
    pivot = find_studio_object(scene, ROLE_PIVOT)
    target = find_studio_object(scene, ROLE_TARGET)
    pivot_loc = tuple(pivot.location) if pivot is not None else (bounds.center[0], bounds.center[1], bounds.floor_z)
    light_plans = studio_core.place_lights(preset, bounds, forward)

    for plan in light_plans:
        light_obj = find_studio_object(scene, plan.role)
        if light_obj is None or light_obj.type != "LIGHT":
            continue
        light_obj.location = _parent_local_position(plan.location, pivot_loc)
        light_obj.rotation_euler = plan.rotation_euler
        ldata = light_obj.data
        ldata.size = plan.size
        ldata.size_y = plan.size_y
        ldata.energy = plan.energy
        ldata.color = plan.color
        if hasattr(ldata, "spread"):
            ldata.spread = plan.spread_rad
        if target is not None:
            _set_track_to(light_obj, target)

    backdrop_obj = find_studio_object(scene, ROLE_BACKDROP)
    if backdrop_obj is not None:
        mat = _ensure_backdrop_material(preset)
        if backdrop_obj.data.materials:
            backdrop_obj.data.materials[0] = mat
        else:
            backdrop_obj.data.materials.append(mat)
        backdrop_obj.hide_viewport = not backdrop
        backdrop_obj.hide_render = not backdrop
        backdrop_obj.hide_set(not backdrop)

    if hdri.is_cs2_sky_world(scene.world):
        return True
    if backdrop or scene.world is None or not scene.world.get("cs2rig_map"):
        scene.world = _ensure_world(preset)
    return True


def frame_subject(context: bpy.types.Context) -> bool:
    """Re-frame the existing studio around the current agent or selected object."""
    scene = context.scene
    collection = find_studio_collection(scene)
    if collection is None:
        return False
    preset = studio_core.get_preset(studio_preset(scene))
    backdrop_on = studio_backdrop_enabled(scene)
    _apply_framing_and_lights(context, collection, preset, backdrop_on)
    return True


def _remove_unused_studio_action() -> None:
    for action in [a for a in bpy.data.actions if a.get(STUDIO_ACTION_PROP) and a.users == 0]:
        bpy.data.actions.remove(action)


def set_turntable(context: bpy.types.Context, enabled: bool) -> bool:
    """Add or remove a seamless 360-degree linear turntable orbit on the studio pivot."""
    scene = context.scene
    pivot = find_studio_object(scene, ROLE_PIVOT)
    if pivot is None:
        return False

    if not enabled:
        if pivot.animation_data is not None:
            pivot.animation_data.action = None
            pivot.animation_data_clear()
        pivot.rotation_euler = (0.0, 0.0, 0.0)
        _remove_unused_studio_action()
        return True

    # Build a fresh linear 360-degree Z-rotation action over the current scene frame range.
    if pivot.animation_data is not None:
        pivot.animation_data.action = None
        pivot.animation_data_clear()
    _remove_unused_studio_action()

    keyframes = studio_core.turntable_keyframes(scene.frame_start, scene.frame_end)
    pivot.rotation_mode = "XYZ"
    for frame, z_angle in keyframes:
        pivot.rotation_euler = (0.0, 0.0, z_angle)
        pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=frame)

    anim = pivot.animation_data
    action = anim.action if anim is not None else None
    if action is not None:
        action.name = STUDIO_ACTION_NAME
        action[STUDIO_ACTION_PROP] = True
        slot = getattr(anim, "action_slot", None)
        for fcurve in _action_fcurves(action, slot):
            fcurve.extrapolation = "LINEAR"
            for kp in fcurve.keyframe_points:
                kp.interpolation = "LINEAR"
            fcurve.update()

    scene.frame_set(scene.frame_current)
    return True


def remove_studio(context: bpy.types.Context) -> bool:
    """Remove all studio objects, collection and unused studio datablocks, restoring previous camera and world."""
    scene = context.scene
    collection = find_studio_collection(scene)
    objects = _studio_objects(scene)
    if collection is None and not objects:
        return False

    # Restore previous active camera and world if the studio currently holds them.
    if collection is not None:
        if scene.camera is not None and is_studio_object(scene.camera):
            prev_cam_name = str(collection.get(PREV_CAMERA_PROP, ""))
            scene.camera = bpy.data.objects.get(prev_cam_name) if prev_cam_name else None
        if scene.world is not None and scene.world.get(STUDIO_WORLD_PROP):
            prev_world_name = str(collection.get(PREV_WORLD_PROP, ""))
            scene.world = bpy.data.worlds.get(prev_world_name) if prev_world_name else None

    datas = {obj.data for obj in objects if obj.data is not None}
    if objects:
        bpy.data.batch_remove(objects)
    if collection is not None:
        # Preserve any user objects or sub-collections placed inside the studio collection.
        for obj in list(collection.objects):
            if obj.name not in scene.collection.objects:
                scene.collection.objects.link(obj)
            collection.objects.unlink(obj)
        for child in list(collection.children):
            if child.name not in scene.collection.children:
                scene.collection.children.link(child)
            collection.children.unlink(child)
        bpy.data.collections.remove(collection)

    orphan_datas = [data for data in datas if data.users == 0]
    if orphan_datas:
        bpy.data.batch_remove(orphan_datas)

    for mat in [m for m in bpy.data.materials if m.get(STUDIO_MAT_PROP) and m.users == 0]:
        bpy.data.materials.remove(mat)
    for world in [w for w in bpy.data.worlds if w.get(STUDIO_WORLD_PROP) and w.users == 0]:
        bpy.data.worlds.remove(world)
    _remove_unused_studio_action()
    return True
