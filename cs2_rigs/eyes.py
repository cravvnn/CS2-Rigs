"""Rebuild CS2 agent eyeball materials in Blender with iris, pupil and sclera shading.

Source2Viewer's glTF export omits the `csgo_eyeball` shader layer (`g_tEyeAlbedo1`,
`g_tEyeMask1` and iris/pupil parameters), leaving imported agents with blank white
sclera. This module restores that layer using two hidden bone-parented eye-frame
empties (so the eyes track animation clips and poses) and a pair of reusable shader
node groups that match the game's projection, iris sizing, hue/saturation and pupil.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from . import importer
from .core import eyes as eyes_core
from .core.eyes import EyeParams
from .session import Session

EYE_NODE_TAG = "cs2rig_eye"
EYE_FRAME_PROP = "cs2rig_eye_frame"
EYE_GROUP_VERSION = 1
EYE_GROUP_VERSION_PROP = "cs2rig_eye_group_version"
UV_GROUP_NAME = "CS2Rig Eye UV"
SHADE_GROUP_NAME = "CS2Rig Eye Shade"
VMAT_PROP = "vmat"
EYE_BONES = {"L": "eyeball_l", "R": "eyeball_r"}
EYE_TARGET_BONE = "eye_target"
HEAD_BONE = "head_0"


def _principled(material: bpy.types.Material) -> bpy.types.ShaderNodeBsdfPrincipled | None:
    if material.node_tree is None:
        return None
    return next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)


def _eye_params(session: Session, material: bpy.types.Material) -> EyeParams | None:
    """Resolve `EyeParams` from a material's `vmat` metadata, falling back to a game dump."""
    vmat = material.get(VMAT_PROP)
    if vmat is None:
        return None
    vmat_path, shader_name, flat = eyes_core.flatten_vmat_prop(vmat)
    params = eyes_core.extract_eye_params(shader_name, flat)
    if params is not None:
        return params
    # A material whose metadata only carries `Name` (or omits params) can still be read from the VPK.
    if vmat_path and not flat:
        try:
            dumped = session.material(vmat_path)
        except RuntimeError:
            return None
        return eyes_core.extract_eye_params(shader_name or eyes_core.EYEBALL_SHADER, dumped)
    return None


def _load_image(path: Path, colour: bool) -> bpy.types.Image:
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = "sRGB" if colour else "Non-Color"
    image.alpha_mode = "CHANNEL_PACKED"
    if image.packed_file is None:
        image.pack()
    return image


def _new_group_socket(group: bpy.types.NodeTree, name: str, in_out: str, socket_type: str, default=None):
    socket = group.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None and hasattr(socket, "default_value"):
        socket.default_value = default
    return socket


def _get_or_create_uv_group() -> bpy.types.NodeTree:
    existing = bpy.data.node_groups.get(UV_GROUP_NAME)
    if existing is not None and existing.get(EYE_GROUP_VERSION_PROP) == EYE_GROUP_VERSION:
        return existing
    if existing is not None:
        existing.nodes.clear()
        existing.interface.clear()
        group = existing
    else:
        group = bpy.data.node_groups.new(UV_GROUP_NAME, "ShaderNodeTree")

    _new_group_socket(group, "Left Coords", "INPUT", "NodeSocketVector")
    _new_group_socket(group, "Right Coords", "INPUT", "NodeSocketVector")
    _new_group_socket(group, "Iris Size", "INPUT", "NodeSocketFloat", eyes_core.DEFAULT_IRIS_SIZE)
    _new_group_socket(group, "Pupil Size", "INPUT", "NodeSocketFloat", eyes_core.DEFAULT_PUPIL_SIZE)
    _new_group_socket(group, "Eye Mask", "INPUT", "NodeSocketFloat", 1.0)
    _new_group_socket(group, "UV", "OUTPUT", "NodeSocketVector")
    _new_group_socket(group, "Pupil Mask", "OUTPUT", "NodeSocketFloat")
    _new_group_socket(group, "Facing", "OUTPUT", "NodeSocketFloat")
    _new_group_socket(group, "Blend", "OUTPUT", "NodeSocketFloat")

    nodes = group.nodes
    links = group.links
    n_in = nodes.new("NodeGroupInput")
    n_in.location = (-1200, 0)
    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (800, 0)

    # Pick whichever eyeball centre is closer to the shading point.
    len_l = nodes.new("ShaderNodeVectorMath")
    len_l.operation = "LENGTH"
    len_l.location = (-950, 220)
    links.new(n_in.outputs["Left Coords"], len_l.inputs[0])

    len_r = nodes.new("ShaderNodeVectorMath")
    len_r.operation = "LENGTH"
    len_r.location = (-950, 60)
    links.new(n_in.outputs["Right Coords"], len_r.inputs[0])

    is_right = nodes.new("ShaderNodeMath")
    is_right.operation = "LESS_THAN"
    is_right.location = (-750, 140)
    links.new(len_r.outputs["Value"], is_right.inputs[0])
    links.new(len_l.outputs["Value"], is_right.inputs[1])

    pick = nodes.new("ShaderNodeMix")
    pick.data_type = "VECTOR"
    pick.location = (-560, 120)
    links.new(is_right.outputs["Value"], pick.inputs[0])
    links.new(n_in.outputs["Left Coords"], pick.inputs[4])
    links.new(n_in.outputs["Right Coords"], pick.inputs[5])

    norm = nodes.new("ShaderNodeVectorMath")
    norm.operation = "NORMALIZE"
    norm.location = (-360, 120)
    links.new(pick.outputs[1], norm.inputs[0])

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-180, 120)
    links.new(norm.outputs["Vector"], sep.inputs["Vector"])

    p2d = nodes.new("ShaderNodeCombineXYZ")
    p2d.location = (0, 220)
    links.new(sep.outputs["X"], p2d.inputs["X"])
    links.new(sep.outputs["Y"], p2d.inputs["Y"])

    radius = nodes.new("ShaderNodeVectorMath")
    radius.operation = "LENGTH"
    radius.location = (180, 60)
    links.new(p2d.outputs["Vector"], radius.inputs[0])

    # Iris warp: mix(p * (2 - iris_size), p, clamp(r, 0, 1))
    iris_factor = nodes.new("ShaderNodeMath")
    iris_factor.operation = "SUBTRACT"
    iris_factor.inputs[0].default_value = 2.0
    iris_factor.location = (0, 420)
    links.new(n_in.outputs["Iris Size"], iris_factor.inputs[1])

    p_scaled = nodes.new("ShaderNodeVectorMath")
    p_scaled.operation = "SCALE"
    p_scaled.location = (180, 320)
    links.new(p2d.outputs["Vector"], p_scaled.inputs[0])
    links.new(iris_factor.outputs["Value"], p_scaled.inputs["Scale"])

    r_clamp = nodes.new("ShaderNodeClamp")
    r_clamp.location = (360, 140)
    links.new(radius.outputs["Value"], r_clamp.inputs["Value"])

    p_iris = nodes.new("ShaderNodeMix")
    p_iris.data_type = "VECTOR"
    p_iris.location = (380, 320)
    links.new(r_clamp.outputs["Result"], p_iris.inputs[0])
    links.new(p_scaled.outputs["Vector"], p_iris.inputs[4])
    links.new(p2d.outputs["Vector"], p_iris.inputs[5])

    uv_xform = nodes.new("ShaderNodeVectorMath")
    uv_xform.operation = "MULTIPLY_ADD"
    uv_xform.inputs[1].default_value = (0.5, 0.5, 0.0)
    uv_xform.inputs[2].default_value = (0.5, 0.5, 0.0)
    uv_xform.location = (580, 320)
    links.new(p_iris.outputs[1], uv_xform.inputs[0])
    links.new(uv_xform.outputs["Vector"], n_out.inputs["UV"])

    # Pupil smoothstep(pupil_size, pupil_size + 0.03, r)
    pupil_hi = nodes.new("ShaderNodeMath")
    pupil_hi.operation = "ADD"
    pupil_hi.inputs[1].default_value = eyes_core.PUPIL_FEATHER
    pupil_hi.location = (180, -100)
    links.new(n_in.outputs["Pupil Size"], pupil_hi.inputs[0])

    pupil_mask = nodes.new("ShaderNodeMapRange")
    pupil_mask.interpolation_type = "SMOOTHSTEP"
    pupil_mask.location = (420, -40)
    links.new(radius.outputs["Value"], pupil_mask.inputs[0])
    links.new(n_in.outputs["Pupil Size"], pupil_mask.inputs[1])
    links.new(pupil_hi.outputs["Value"], pupil_mask.inputs[2])
    pupil_mask.inputs[3].default_value = 0.0
    pupil_mask.inputs[4].default_value = 1.0
    links.new(pupil_mask.outputs[0], n_out.inputs["Pupil Mask"])

    # Facing = saturate(dot(eye_fwd, N_sphere)) = clamp(nz, 0, 1)
    facing = nodes.new("ShaderNodeClamp")
    facing.location = (180, -260)
    links.new(sep.outputs["Z"], facing.inputs["Value"])
    links.new(facing.outputs["Result"], n_out.inputs["Facing"])

    # Blend = eye_mask * smoothstep(0.1, 0.3, facing)
    facing_blend = nodes.new("ShaderNodeMapRange")
    facing_blend.interpolation_type = "SMOOTHSTEP"
    facing_blend.inputs[1].default_value = 0.1
    facing_blend.inputs[2].default_value = 0.3
    facing_blend.inputs[3].default_value = 0.0
    facing_blend.inputs[4].default_value = 1.0
    facing_blend.location = (380, -280)
    links.new(facing.outputs["Result"], facing_blend.inputs[0])

    blend = nodes.new("ShaderNodeMath")
    blend.operation = "MULTIPLY"
    blend.use_clamp = True
    blend.location = (580, -280)
    links.new(n_in.outputs["Eye Mask"], blend.inputs[0])
    links.new(facing_blend.outputs[0], blend.inputs[1])
    links.new(blend.outputs["Value"], n_out.inputs["Blend"])

    group[EYE_GROUP_VERSION_PROP] = EYE_GROUP_VERSION
    return group


def _get_or_create_shade_group() -> bpy.types.NodeTree:
    existing = bpy.data.node_groups.get(SHADE_GROUP_NAME)
    if existing is not None and existing.get(EYE_GROUP_VERSION_PROP) == EYE_GROUP_VERSION:
        return existing
    if existing is not None:
        existing.nodes.clear()
        existing.interface.clear()
        group = existing
    else:
        group = bpy.data.node_groups.new(SHADE_GROUP_NAME, "ShaderNodeTree")

    _new_group_socket(group, "Base Color", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _new_group_socket(group, "Roughness", "INPUT", "NodeSocketFloat", 0.5)
    _new_group_socket(group, "Metallic", "INPUT", "NodeSocketFloat", 0.0)
    _new_group_socket(group, "Normal", "INPUT", "NodeSocketVector")
    _new_group_socket(group, "Has Normal", "INPUT", "NodeSocketFloat", 0.0)
    _new_group_socket(group, "Eye Color", "INPUT", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _new_group_socket(group, "Iris Mask", "INPUT", "NodeSocketFloat", 0.0)
    _new_group_socket(group, "Pupil Mask", "INPUT", "NodeSocketFloat", 1.0)
    _new_group_socket(group, "Facing", "INPUT", "NodeSocketFloat", 1.0)
    _new_group_socket(group, "Blend", "INPUT", "NodeSocketFloat", 0.0)
    _new_group_socket(group, "Color Row R", "INPUT", "NodeSocketVector", (1.0, 0.0, 0.0))
    _new_group_socket(group, "Color Row G", "INPUT", "NodeSocketVector", (0.0, 1.0, 0.0))
    _new_group_socket(group, "Color Row B", "INPUT", "NodeSocketVector", (0.0, 0.0, 1.0))

    _new_group_socket(group, "Base Color", "OUTPUT", "NodeSocketColor")
    _new_group_socket(group, "Roughness", "OUTPUT", "NodeSocketFloat")
    _new_group_socket(group, "Metallic", "OUTPUT", "NodeSocketFloat")
    _new_group_socket(group, "Normal", "OUTPUT", "NodeSocketVector")

    nodes = group.nodes
    links = group.links
    n_in = nodes.new("NodeGroupInput")
    n_in.location = (-950, 0)
    n_out = nodes.new("NodeGroupOutput")
    n_out.location = (700, 0)

    # Apply combined 3x3 hue-shift + saturation matrix to linear RGB, clamped >= 0.
    dot_r = nodes.new("ShaderNodeVectorMath")
    dot_r.operation = "DOT_PRODUCT"
    dot_r.location = (-650, 320)
    links.new(n_in.outputs["Eye Color"], dot_r.inputs[0])
    links.new(n_in.outputs["Color Row R"], dot_r.inputs[1])

    dot_g = nodes.new("ShaderNodeVectorMath")
    dot_g.operation = "DOT_PRODUCT"
    dot_g.location = (-650, 180)
    links.new(n_in.outputs["Eye Color"], dot_g.inputs[0])
    links.new(n_in.outputs["Color Row G"], dot_g.inputs[1])

    dot_b = nodes.new("ShaderNodeVectorMath")
    dot_b.operation = "DOT_PRODUCT"
    dot_b.location = (-650, 40)
    links.new(n_in.outputs["Eye Color"], dot_b.inputs[0])
    links.new(n_in.outputs["Color Row B"], dot_b.inputs[1])

    comb = nodes.new("ShaderNodeCombineXYZ")
    comb.location = (-440, 180)
    links.new(dot_r.outputs["Value"], comb.inputs["X"])
    links.new(dot_g.outputs["Value"], comb.inputs["Y"])
    links.new(dot_b.outputs["Value"], comb.inputs["Z"])

    clamp_pos = nodes.new("ShaderNodeVectorMath")
    clamp_pos.operation = "MAXIMUM"
    clamp_pos.inputs[1].default_value = (0.0, 0.0, 0.0)
    clamp_pos.location = (-260, 180)
    links.new(comb.outputs["Vector"], clamp_pos.inputs[0])

    # Mask hue/saturation adjustment to the iris alpha so sclera keeps its natural tone.
    iris_tint = nodes.new("ShaderNodeMix")
    iris_tint.data_type = "RGBA"
    iris_tint.location = (-60, 220)
    links.new(n_in.outputs["Iris Mask"], iris_tint.inputs[0])
    links.new(n_in.outputs["Eye Color"], iris_tint.inputs[6])
    links.new(clamp_pos.outputs["Vector"], iris_tint.inputs[7])

    shade_mul = nodes.new("ShaderNodeMath")
    shade_mul.operation = "MULTIPLY"
    shade_mul.location = (-60, -20)
    links.new(n_in.outputs["Pupil Mask"], shade_mul.inputs[0])
    links.new(n_in.outputs["Facing"], shade_mul.inputs[1])

    eye_lit = nodes.new("ShaderNodeVectorMath")
    eye_lit.operation = "SCALE"
    eye_lit.location = (140, 140)
    links.new(iris_tint.outputs[2], eye_lit.inputs[0])
    links.new(shade_mul.outputs["Value"], eye_lit.inputs["Scale"])

    mix_col = nodes.new("ShaderNodeMix")
    mix_col.data_type = "RGBA"
    mix_col.location = (380, 260)
    links.new(n_in.outputs["Blend"], mix_col.inputs[0])
    links.new(n_in.outputs["Base Color"], mix_col.inputs[6])
    links.new(eye_lit.outputs["Vector"], mix_col.inputs[7])
    links.new(mix_col.outputs[2], n_out.inputs["Base Color"])

    mix_rough = nodes.new("ShaderNodeMix")
    mix_rough.data_type = "FLOAT"
    mix_rough.inputs[3].default_value = eyes_core.EYE_ROUGHNESS
    mix_rough.location = (380, 40)
    links.new(n_in.outputs["Blend"], mix_rough.inputs[0])
    links.new(n_in.outputs["Roughness"], mix_rough.inputs[2])
    links.new(mix_rough.outputs[0], n_out.inputs["Roughness"])

    mix_metal = nodes.new("ShaderNodeMix")
    mix_metal.data_type = "FLOAT"
    mix_metal.inputs[3].default_value = 0.0
    mix_metal.location = (380, -140)
    links.new(n_in.outputs["Blend"], mix_metal.inputs[0])
    links.new(n_in.outputs["Metallic"], mix_metal.inputs[2])
    links.new(mix_metal.outputs[0], n_out.inputs["Metallic"])

    # Clear the head skin's normal map over the smooth wet eyeball.
    geom = nodes.new("ShaderNodeNewGeometry")
    geom.location = (-260, -340)

    orig_norm = nodes.new("ShaderNodeMix")
    orig_norm.data_type = "VECTOR"
    orig_norm.location = (-20, -320)
    links.new(n_in.outputs["Has Normal"], orig_norm.inputs[0])
    links.new(geom.outputs["Normal"], orig_norm.inputs[4])
    links.new(n_in.outputs["Normal"], orig_norm.inputs[5])

    sqrt_blend = nodes.new("ShaderNodeMath")
    sqrt_blend.operation = "SQRT"
    sqrt_blend.location = (-20, -520)
    links.new(n_in.outputs["Blend"], sqrt_blend.inputs[0])

    mix_norm = nodes.new("ShaderNodeMix")
    mix_norm.data_type = "VECTOR"
    mix_norm.location = (220, -360)
    links.new(sqrt_blend.outputs["Value"], mix_norm.inputs[0])
    links.new(orig_norm.outputs[1], mix_norm.inputs[4])
    links.new(geom.outputs["Normal"], mix_norm.inputs[5])

    norm_out = nodes.new("ShaderNodeVectorMath")
    norm_out.operation = "NORMALIZE"
    norm_out.location = (440, -360)
    links.new(mix_norm.outputs[1], norm_out.inputs[0])
    links.new(norm_out.outputs["Vector"], n_out.inputs["Normal"])

    group[EYE_GROUP_VERSION_PROP] = EYE_GROUP_VERSION
    return group


def _ensure_eye_frame(
    armature: bpy.types.Object,
    side: str,
    frame_matrix: Matrix,
    parent_bone_name: str,
) -> bpy.types.Object:
    """Create or update one hidden bone-parented Empty carrying an eyeball's local frame."""
    existing = next(
        (obj for obj in armature.children if obj.type == "EMPTY" and obj.get(EYE_FRAME_PROP) == side),
        None,
    )
    if existing is None:
        base_name = armature.name.removesuffix(".skeleton")
        existing = bpy.data.objects.new(f"{base_name}.eye_{side.lower()}", None)
        existing.empty_display_type = "PLAIN_AXES"
        existing.empty_display_size = 0.01
        existing[EYE_FRAME_PROP] = side
        collection = armature.users_collection[0] if armature.users_collection else bpy.context.scene.collection
        collection.objects.link(existing)
    existing.hide_viewport = True
    existing.hide_render = True

    bone = armature.data.bones.get(parent_bone_name) or armature.data.bones.get(HEAD_BONE)
    existing.parent = armature
    if bone is not None:
        existing.parent_type = "BONE"
        existing.parent_bone = bone.name
        # Undo Blender's bone-tail offset and go from the bone's rest frame to the eyeball frame.
        existing.matrix_parent_inverse = (
            Matrix.Translation((0.0, -bone.length, 0.0))
            @ bone.matrix_local.inverted()
            @ frame_matrix
        )
        existing.matrix_basis.identity()
    else:
        existing.parent_type = "OBJECT"
        existing.matrix_parent_inverse.identity()
        existing.matrix_basis = frame_matrix
    return existing


def _build_eye_frames(armature: bpy.types.Object, params: EyeParams) -> tuple[bpy.types.Object, bpy.types.Object] | None:
    """Build or update the left and right eye-frame empties on `armature`."""
    bones = armature.data.bones
    bone_l = bones.get(EYE_BONES["L"])
    bone_r = bones.get(EYE_BONES["R"])
    if bone_l is None or bone_r is None:
        return None
    pos_l = tuple(bone_l.head_local)
    pos_r = tuple(bone_r.head_local)
    target_bone = bones.get(EYE_TARGET_BONE)
    if target_bone is not None:
        target_pos = tuple(target_bone.head_local)
    else:
        mid = ((pos_l[0] + pos_r[0]) * 0.5, (pos_l[1] + pos_r[1]) * 0.5, (pos_l[2] + pos_r[2]) * 0.5)
        target_pos = (mid[0], mid[1] - 2.5, mid[2])

    mat_l = Matrix(
        eyes_core.eye_frame_matrix(
            pos_l,
            target_pos,
            params.radius,
            walleye_deg=params.walleye_l,
            point_at_target=params.point_at_target,
        )
    )
    mat_r = Matrix(
        eyes_core.eye_frame_matrix(
            pos_r,
            target_pos,
            params.radius,
            walleye_deg=-params.walleye_r,
            point_at_target=params.point_at_target,
        )
    )
    obj_l = _ensure_eye_frame(armature, "L", mat_l, EYE_BONES["L"])
    obj_r = _ensure_eye_frame(armature, "R", mat_r, EYE_BONES["R"])
    return obj_l, obj_r


def _unwrap_existing(tree: bpy.types.NodeTree, shader: bpy.types.ShaderNodeBsdfPrincipled) -> None:
    """Remove a previous eye-layer node setup, restoring the original links into `shader`."""
    shade_node = next(
        (n for n in tree.nodes if n.get(EYE_NODE_TAG) and n.type == "GROUP" and n.node_tree and n.node_tree.name == SHADE_GROUP_NAME),
        None,
    )
    if shade_node is not None:
        for sock_name in ("Base Color", "Roughness", "Metallic", "Normal"):
            dst = shader.inputs[sock_name]
            src_in = shade_node.inputs.get(sock_name)
            if src_in is None:
                continue
            for link in list(dst.links):
                tree.links.remove(link)
            if src_in.is_linked:
                tree.links.new(src_in.links[0].from_socket, dst)
            elif sock_name != "Normal":
                dst.default_value = src_in.default_value
    for node in [n for n in tree.nodes if n.get(EYE_NODE_TAG)]:
        tree.nodes.remove(node)


def _ensure_local_material(armature: bpy.types.Object, material: bpy.types.Material) -> bpy.types.Material:
    """Copy `material` if another object outside `armature` also uses it, so eye empties stay per-agent."""
    own_meshes = {obj for obj in armature.children_recursive if obj.type == "MESH"}
    shared = any(
        obj not in own_meshes and obj.type == "MESH" and any(s.material == material for s in obj.material_slots)
        for obj in bpy.data.objects
    )
    if not shared:
        return material
    copy = material.copy()
    for obj in own_meshes:
        for slot in obj.material_slots:
            if slot.material == material:
                slot.material = copy
    return copy


def _apply_eye_nodes(
    session: Session,
    material: bpy.types.Material,
    params: EyeParams,
    eye_l: bpy.types.Object,
    eye_r: bpy.types.Object,
) -> bool:
    """Wire the eye projection and shading nodes into `material`."""
    shader = _principled(material)
    if shader is None:
        return False
    tree = material.node_tree
    _unwrap_existing(tree, shader)

    albedo_path = session.texture(params.albedo_texture)
    albedo_img = _load_image(albedo_path, colour=True)
    mask_img = None
    if params.mask_texture:
        try:
            mask_img = _load_image(session.texture(params.mask_texture), colour=False)
        except RuntimeError:
            mask_img = None

    uv_group = _get_or_create_uv_group()
    shade_group = _get_or_create_shade_group()

    nodes = tree.nodes
    links = tree.links
    base_x = shader.location.x
    base_y = shader.location.y

    def tagged(node_type: str, x: float, y: float):
        node = nodes.new(node_type)
        node.location = (x, y)
        node[EYE_NODE_TAG] = True
        return node

    tc_l = tagged("ShaderNodeTexCoord", base_x - 1020, base_y + 420)
    tc_l.object = eye_l

    tc_r = tagged("ShaderNodeTexCoord", base_x - 1020, base_y + 160)
    tc_r.object = eye_r

    uv_node = tagged("ShaderNodeGroup", base_x - 760, base_y + 280)
    uv_node.node_tree = uv_group
    uv_node.inputs["Iris Size"].default_value = params.iris_size
    uv_node.inputs["Pupil Size"].default_value = params.pupil_size
    links.new(tc_l.outputs["Object"], uv_node.inputs["Left Coords"])
    links.new(tc_r.outputs["Object"], uv_node.inputs["Right Coords"])

    if mask_img is not None:
        mask_tex = tagged("ShaderNodeTexImage", base_x - 1060, base_y - 120)
        mask_tex.image = mask_img
        mask_sep = tagged("ShaderNodeSeparateColor", base_x - 800, base_y - 120)
        links.new(mask_tex.outputs["Color"], mask_sep.inputs["Color"])
        links.new(mask_sep.outputs["Red"], uv_node.inputs["Eye Mask"])
    else:
        uv_node.inputs["Eye Mask"].default_value = 1.0

    albedo_tex = tagged("ShaderNodeTexImage", base_x - 520, base_y + 440)
    albedo_tex.image = albedo_img
    albedo_tex.extension = "MIRROR"
    links.new(uv_node.outputs["UV"], albedo_tex.inputs["Vector"])

    shade_node = tagged("ShaderNodeGroup", base_x - 220, base_y + 220)
    shade_node.node_tree = shade_group
    row_r, row_g, row_b = eyes_core.iris_color_matrix(params.hue_shift, params.saturation)
    shade_node.inputs["Color Row R"].default_value = row_r
    shade_node.inputs["Color Row G"].default_value = row_g
    shade_node.inputs["Color Row B"].default_value = row_b

    # Splice shade_node in front of Principled BSDF while preserving all upstream head links.
    for sock_name in ("Base Color", "Roughness", "Metallic"):
        dst = shader.inputs[sock_name]
        if dst.is_linked:
            src_sock = dst.links[0].from_socket
            links.remove(dst.links[0])
            links.new(src_sock, shade_node.inputs[sock_name])
        else:
            shade_node.inputs[sock_name].default_value = dst.default_value
        links.new(shade_node.outputs[sock_name], dst)

    norm_dst = shader.inputs["Normal"]
    if norm_dst.is_linked:
        src_norm = norm_dst.links[0].from_socket
        links.remove(norm_dst.links[0])
        links.new(src_norm, shade_node.inputs["Normal"])
        shade_node.inputs["Has Normal"].default_value = 1.0
        links.new(shade_node.outputs["Normal"], norm_dst)
    else:
        shade_node.inputs["Has Normal"].default_value = 0.0

    links.new(albedo_tex.outputs["Color"], shade_node.inputs["Eye Color"])
    links.new(albedo_tex.outputs["Alpha"], shade_node.inputs["Iris Mask"])
    links.new(uv_node.outputs["Pupil Mask"], shade_node.inputs["Pupil Mask"])
    links.new(uv_node.outputs["Facing"], shade_node.inputs["Facing"])
    links.new(uv_node.outputs["Blend"], shade_node.inputs["Blend"])
    return True


def rebuild_agent_eyes(session: Session, armature: bpy.types.Object) -> int:
    """Rebuild every eye material on `armature`. Returns the number of materials rebuilt."""
    candidates: list[tuple[bpy.types.Material, EyeParams]] = []
    seen: set[int] = set()
    for obj in armature.children_recursive:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or id(mat) in seen:
                continue
            seen.add(id(mat))
            params = _eye_params(session, mat)
            if params is not None:
                candidates.append((mat, params))
    if not candidates:
        return 0

    rebuilt = 0
    frames: tuple[bpy.types.Object, bpy.types.Object] | None = None
    for mat, params in candidates:
        local_mat = _ensure_local_material(armature, mat)
        if frames is None:
            frames = _build_eye_frames(armature, params)
            if frames is None:
                return 0
        if _apply_eye_nodes(session, local_mat, params, frames[0], frames[1]):
            rebuilt += 1
    return rebuilt


def selected_agents(context: bpy.types.Context) -> list[bpy.types.Object]:
    """Every unique imported agent armature among the selected objects, else the active/sole one."""
    agents: list[bpy.types.Object] = []
    seen: set[int] = set()
    for obj in (context.active_object, *context.selected_objects):
        armature = importer._agent_above(obj)
        if armature is not None and id(armature) not in seen:
            seen.add(id(armature))
            agents.append(armature)
    if agents:
        return agents
    sole = importer.find_agent_armature(context)
    return [sole] if sole is not None else []


def fix_selected_eyes(context: bpy.types.Context, session: Session) -> tuple[int, int]:
    """Rebuild eye materials on all selected agent(s). Returns (agents_fixed, materials_rebuilt)."""
    agents = selected_agents(context)
    agents_fixed = 0
    materials_rebuilt = 0
    for armature in agents:
        count = rebuild_agent_eyes(session, armature)
        if count > 0:
            agents_fixed += 1
            materials_rebuilt += count
    return agents_fixed, materials_rebuilt
