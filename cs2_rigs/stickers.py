"""UV1/UV2 shader-node sticker layering on held and standalone CS2 weapons.

Matches Valve's csgo_weapon_vulkan_50_ps.glsl sticker pipeline:
- Playside UV1 gate (u1 > -1.0) + per-body g_tStickerWepInputs mask & cavity
- CHANNEL_PACKED alpha cutout + exact scrape/wear math with paper backing reveal
- Metallic foil normal/roughness and view-dependent Holographic spectrum shift
- Seamless sticker_gaps mesh bridging
- Orthogonal to paint.py: survives finish swaps and legacy <-> HD body changes
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy

from . import importer
from .core import catalog as catalog_mod
from .core import stickers as stickers_mod
from .core.stickers import (
    MAX_SLOTS,
    SCRATCHES_VTEX,
    STICKER_BASE_MATERIAL_PROP,
    STICKERS_PROP,
    AppliedSticker,
    BodyStickerDefaults,
    StickerKit,
    StickerSlotDefault,
)
from .session import Session

STICKER_SLOT_PROP = "cs2rig_sticker_slot"
STICKER_GROUP_PREFIX = "CS2_StickerLayer"
_FALLBACK_OFFSET = (0.0, -0.4)
_FALLBACK_SCALE = 14.1


def _principled(material: bpy.types.Material | None):
    if material is None or material.node_tree is None:
        return None
    return next((node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)


def _game_material(material: bpy.types.Material | None) -> str:
    if material is None:
        return ""
    source = material.get("vmat")
    try:
        return str(source["Name"])
    except (TypeError, KeyError):
        return ""


def _sticker_base_material(material: bpy.types.Material | None) -> bpy.types.Material | None:
    if material is None or STICKER_BASE_MATERIAL_PROP not in material.keys():
        return None
    base = material[STICKER_BASE_MATERIAL_PROP]
    if isinstance(base, str):
        base = bpy.data.materials.get(base)
    return base if isinstance(base, bpy.types.Material) and base != material else None


def _unwrap_material(material: bpy.types.Material | None) -> bpy.types.Material | None:
    cur = material
    seen = set()
    while cur is not None and cur.name not in seen:
        seen.add(cur.name)
        nxt = _sticker_base_material(cur)
        if nxt is None:
            return cur
        cur = nxt
    return cur


def _load_image_packed(path: Path, colorspace: str = "sRGB", channel_packed: bool = True) -> bpy.types.Image:
    img = bpy.data.images.load(str(path), check_existing=True)
    img.colorspace_settings.name = colorspace
    if channel_packed:
        img.alpha_mode = "CHANNEL_PACKED"
    if img.packed_file is None:
        img.pack()
    return img


def _active_body(target: bpy.types.Object) -> tuple[bpy.types.Object | None, str]:
    """Return the active weapon body mesh and its kind ('legacy' or 'current')."""
    meshes = [obj for obj in importer.weapon_objects(target) if obj.type == "MESH"]
    from .core import targets

    legacy, current = targets.select_weapon_bodies(meshes)
    if legacy is not None and not legacy.hide_render and not legacy.hide_get():
        return legacy, "legacy"
    if current is not None and not current.hide_render and not current.hide_get():
        return current, "current"
    if current is not None:
        return current, "current"
    if legacy is not None:
        return legacy, "legacy"
    return None, "current"


def _find_body_slots(body: bpy.types.Object):
    """Find the weapon body's primary material slot, optional sticker_gaps slot, and base .vmat path."""
    main_slot = None
    gap_slot = None
    body_vmat = ""
    for sl in body.material_slots:
        if sl.material is None:
            continue
        clean = _unwrap_material(sl.material) or sl.material
        vm = _game_material(clean) or _game_material(sl.material)
        if "sticker_gaps" in clean.name.lower() or "sticker_gaps" in vm.lower():
            gap_slot = sl
        elif main_slot is None and _principled(clean) is not None:
            main_slot = sl
            body_vmat = vm
    return main_slot, gap_slot, body_vmat


def _weapon_model_path(session: Session, target: bpy.types.Object) -> str:
    key = importer.weapon_key(target)
    if not key:
        return ""
    weapon = next((w for w in catalog_mod.catalog_weapons(session.catalog()) if w.key == key), None)
    return weapon.model_path if weapon is not None else ""


def body_defaults(session: Session, target: bpy.types.Object) -> BodyStickerDefaults | None:
    """Resolve runtime slot defaults (0..4) + StickerMarkup schemas for the active weapon body."""
    body, body_kind = _active_body(target)
    if body is None:
        return None
    _main, _gap, body_vmat = _find_body_slots(body)
    if not body_vmat:
        return None
    vmat_values = session.material(body_vmat)
    defaults = stickers_mod.parse_body_vmat_slots(vmat_values)
    model_path = _weapon_model_path(session, target)
    if model_path:
        markup = session.sticker_markup(model_path)
        defaults = stickers_mod.merge_body_slots(defaults, markup.get(body_kind))
    return defaults


def applied_stickers(target: bpy.types.Object | None) -> list[AppliedSticker]:
    """Read the applied sticker list from the weapon root's custom property."""
    root = importer.weapon_root(target) if target is not None else None
    if root is None or STICKERS_PROP not in root.keys():
        return []
    return stickers_mod.deserialize_applied(str(root[STICKERS_PROP]))


def slot_sticker(target: bpy.types.Object | None, slot: int) -> AppliedSticker | None:
    return next((s for s in applied_stickers(target) if s.slot == slot), None)


def _store_applied(target: bpy.types.Object, items: list[AppliedSticker]) -> None:
    root = importer.weapon_root(target)
    if root is None:
        return
    if items:
        root[STICKERS_PROP] = stickers_mod.serialize_applied(items)
    elif STICKERS_PROP in root.keys():
        del root[STICKERS_PROP]


def strip_sticker_materials(target: bpy.types.Object) -> None:
    """Unwrap sticker-layered materials back to their underlying stock/painted materials."""
    removed_groups: list[bpy.types.NodeTree] = []
    for obj in importer.weapon_objects(target):
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or STICKER_BASE_MATERIAL_PROP not in mat.keys():
                continue
            base = _sticker_base_material(mat)
            if base is None:
                continue
            if mat.node_tree is not None:
                for node in mat.node_tree.nodes:
                    if node.type == "GROUP" and node.node_tree is not None and node.node_tree.name.startswith(STICKER_GROUP_PREFIX):
                        removed_groups.append(node.node_tree)
            slot.material = base
            if mat.users == 0:
                bpy.data.materials.remove(mat)
    for ng in removed_groups:
        if ng.users == 0:
            bpy.data.node_groups.remove(ng)


def _uv1_name(body: bpy.types.Object) -> str:
    uv_layers = body.data.uv_layers
    if "UVMap.001" in uv_layers:
        return "UVMap.001"
    if len(uv_layers) >= 2:
        return uv_layers[1].name
    return uv_layers[0].name if uv_layers else "UVMap.001"


def _uv0_name(body: bpy.types.Object) -> str:
    uv_layers = body.data.uv_layers
    if "UVMap" in uv_layers:
        return "UVMap"
    return uv_layers[0].name if uv_layers else "UVMap"


def build_sticker_layer_nodegroup(
    name: str,
    spec: dict,
    albedo_img: bpy.types.Image,
    nrm_rough_img: bpy.types.Image,
    scratches_img: bpy.types.Image,
    wep_inputs_img: bpy.types.Image | None,
    sfx_mask_img: bpy.types.Image | None,
    holo_spec_img: bpy.types.Image | None,
    offset: tuple[float, float],
    scale: float,
    rotation_turns: float,
    wear: float,
    *,
    uv0_name: str = "UVMap",
    uv1_name: str = "UVMap.001",
    is_gap_mat: bool = False,
) -> bpy.types.ShaderNodeTree:
    """Create a ShaderNodeTree matching Valve's csgo_weapon_vulkan_50_ps.glsl sticker layer."""
    ng = bpy.data.node_groups.new(name, "ShaderNodeTree")

    iface = ng.interface
    iface.new_socket("Base Color", in_out="INPUT", socket_type="NodeSocketColor")
    iface.new_socket("Base Roughness", in_out="INPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Base Metallic", in_out="INPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Base Normal", in_out="INPUT", socket_type="NodeSocketVector")
    iface.new_socket("Base Alpha", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 0.0 if is_gap_mat else 1.0
    iface.new_socket("Offset X", in_out="INPUT", socket_type="NodeSocketFloat").default_value = float(offset[0])
    iface.new_socket("Offset Y", in_out="INPUT", socket_type="NodeSocketFloat").default_value = float(offset[1])
    iface.new_socket("Scale", in_out="INPUT", socket_type="NodeSocketFloat").default_value = float(scale)
    iface.new_socket("Rotation Turns", in_out="INPUT", socket_type="NodeSocketFloat").default_value = float(rotation_turns)
    iface.new_socket("Wear", in_out="INPUT", socket_type="NodeSocketFloat").default_value = float(wear)

    iface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    iface.new_socket("Roughness", in_out="OUTPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Metallic", in_out="OUTPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    iface.new_socket("Alpha", in_out="OUTPUT", socket_type="NodeSocketFloat")

    nodes = ng.nodes
    links = ng.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")

    def math_node(op: str, a=None, b=None, clamp: bool = False):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.use_clamp = clamp
        if a is not None:
            if isinstance(a, (int, float)):
                n.inputs[0].default_value = float(a)
            else:
                links.new(a, n.inputs[0])
        if b is not None:
            if isinstance(b, (int, float)):
                n.inputs[1].default_value = float(b)
            else:
                links.new(b, n.inputs[1])
        return n.outputs[0]

    def map_smoothstep(val, low, high):
        n = nodes.new("ShaderNodeMapRange")
        n.interpolation_type = "SMOOTHSTEP"
        n.clamp = True
        links.new(val, n.inputs["Value"])
        if isinstance(low, (int, float)):
            n.inputs["From Min"].default_value = float(low)
        else:
            links.new(low, n.inputs["From Min"])
        if isinstance(high, (int, float)):
            n.inputs["From Max"].default_value = float(high)
        else:
            links.new(high, n.inputs["From Max"])
        n.inputs["To Min"].default_value = 0.0
        n.inputs["To Max"].default_value = 1.0
        return n.outputs["Result"]

    def mix_float(a, b, fac):
        n = nodes.new("ShaderNodeMix")
        n.data_type = "FLOAT"
        n.clamp_factor = True
        if isinstance(fac, (int, float)):
            n.inputs["Factor"].default_value = float(fac)
        else:
            links.new(fac, n.inputs["Factor"])
        if isinstance(a, (int, float)):
            n.inputs["A"].default_value = float(a)
        else:
            links.new(a, n.inputs["A"])
        if isinstance(b, (int, float)):
            n.inputs["B"].default_value = float(b)
        else:
            links.new(b, n.inputs["B"])
        return n.outputs["Result"]

    def mix_color(a, b, fac, blend: str = "MIX"):
        n = nodes.new("ShaderNodeMix")
        n.data_type = "RGBA"
        n.blend_type = blend
        n.clamp_factor = True
        if isinstance(fac, (int, float)):
            n.inputs["Factor"].default_value = float(fac)
        else:
            links.new(fac, n.inputs["Factor"])
        if isinstance(a, tuple):
            n.inputs["A"].default_value = a
        else:
            links.new(a, n.inputs["A"])
        if isinstance(b, tuple):
            n.inputs["B"].default_value = b
        else:
            links.new(b, n.inputs["B"])
        return n.outputs["Result"]

    # 1. UV1 (TEXCOORD_1) -> Source 2 top-down space & sticker transform
    uv1 = nodes.new("ShaderNodeUVMap")
    uv1.uv_map = uv1_name
    sep1 = nodes.new("ShaderNodeSeparateXYZ")
    links.new(uv1.outputs["UV"], sep1.inputs["Vector"])
    u1 = sep1.outputs["X"]
    v1_s2 = math_node("SUBTRACT", 1.0, sep1.outputs["Y"])

    # Playside gate: u1 > -1.0 (backside is placed in [-2, -1])
    is_playside = math_node("GREATER_THAN", u1, -1.0)

    # p = ((uv1 - 0.5) - offset) * scale
    u_centered = math_node("SUBTRACT", math_node("SUBTRACT", u1, 0.5), gin.outputs["Offset X"])
    v_centered = math_node("SUBTRACT", math_node("SUBTRACT", v1_s2, 0.5), gin.outputs["Offset Y"])
    px = math_node("MULTIPLY", u_centered, gin.outputs["Scale"])
    py = math_node("MULTIPLY", v_centered, gin.outputs["Scale"])

    # Rotation: angle = rotation_turns * 2 * pi
    angle = math_node("MULTIPLY", gin.outputs["Rotation Turns"], 2.0 * math.pi)
    c_ang = math_node("COSINE", angle)
    s_ang = math_node("SINE", angle)
    su = math_node("ADD", math_node("SUBTRACT", math_node("MULTIPLY", px, c_ang), math_node("MULTIPLY", py, s_ang)), 0.5)
    sv_s2 = math_node("ADD", math_node("ADD", math_node("MULTIPLY", px, s_ang), math_node("MULTIPLY", py, c_ang)), 0.5)

    # Strict bounds mask: su, sv_s2 in [0, 1] and is_playside
    in_u = math_node("MULTIPLY", math_node("GREATER_THAN", su, 0.0), math_node("LESS_THAN", su, 1.0))
    in_v = math_node("MULTIPLY", math_node("GREATER_THAN", sv_s2, 0.0), math_node("LESS_THAN", sv_s2, 1.0))
    in_bounds = math_node("MULTIPLY", math_node("MULTIPLY", in_u, in_v), is_playside)

    # Clamped Blender texture UV (bottom-up)
    su_c = math_node("ADD", su, 0.0, clamp=True)
    sv_c = math_node("ADD", sv_s2, 0.0, clamp=True)
    sv_bl = math_node("SUBTRACT", 1.0, sv_c)
    comb_uv = nodes.new("ShaderNodeCombineXYZ")
    links.new(su_c, comb_uv.inputs["X"])
    links.new(sv_bl, comb_uv.inputs["Y"])
    sticker_uv = comb_uv.outputs["Vector"]

    # 2. Weapon Sticker Inputs (g_tStickerWepInputs on UV0): R=stickerMask, G=stickerCavity
    if wep_inputs_img is not None and not is_gap_mat:
        uv0 = nodes.new("ShaderNodeUVMap")
        uv0.uv_map = uv0_name
        wep_tex = nodes.new("ShaderNodeTexImage")
        wep_tex.image = wep_inputs_img
        links.new(uv0.outputs["UV"], wep_tex.inputs["Vector"])
        wep_sep = nodes.new("ShaderNodeSeparateColor")
        links.new(wep_tex.outputs["Color"], wep_sep.inputs["Color"])
        sticker_mask = wep_sep.outputs["Red"]
        sticker_cavity = wep_sep.outputs["Green"]
    else:
        sticker_mask = 1.0
        sticker_cavity = 0.5

    # 3. Sticker Albedo + WearMask (Alpha, CHANNEL_PACKED)
    alb_tex = nodes.new("ShaderNodeTexImage")
    alb_tex.image = albedo_img
    alb_tex.extension = "EXTEND"
    links.new(sticker_uv, alb_tex.inputs["Vector"])
    sticker_rgb = alb_tex.outputs["Color"]
    wear_mask = alb_tex.outputs["Alpha"]

    cutout = math_node("MULTIPLY", wear_mask, 12.75, clamp=True)
    base_alpha = math_node("MULTIPLY", math_node("MULTIPLY", cutout, sticker_mask), in_bounds)

    # 4. Sticker Normal + Roughness (decoded RGB = tangent normal, Alpha = roughness)
    nrm_tex = nodes.new("ShaderNodeTexImage")
    nrm_tex.image = nrm_rough_img
    nrm_tex.extension = "EXTEND"
    links.new(sticker_uv, nrm_tex.inputs["Vector"])
    sticker_rough = nrm_tex.outputs["Alpha"]

    # Source 2 DirectX normal (-Y) -> Blender OpenGL (+Y)
    nrm_sep = nodes.new("ShaderNodeSeparateColor")
    links.new(nrm_tex.outputs["Color"], nrm_sep.inputs["Color"])
    nrm_comb = nodes.new("ShaderNodeCombineColor")
    links.new(nrm_sep.outputs["Red"], nrm_comb.inputs["Red"])
    links.new(math_node("SUBTRACT", 1.0, nrm_sep.outputs["Green"]), nrm_comb.inputs["Green"])
    links.new(nrm_sep.outputs["Blue"], nrm_comb.inputs["Blue"])

    nrm_map = nodes.new("ShaderNodeNormalMap")
    nrm_map.space = "TANGENT"
    nrm_map.uv_map = uv1_name
    nrm_map.inputs["Strength"].default_value = 1.0
    links.new(nrm_comb.outputs["Color"], nrm_map.inputs["Color"])
    sticker_normal = nrm_map.outputs["Normal"]

    # 5. Metallic / Foil & Color Tint
    is_metallic = spec["is_metallic"]
    is_holo = spec["is_holo"]
    paper_backing = spec["paper_backing"]
    preserve_rough = spec["preserve_roughness"]
    color_tint = spec["color_tint"]
    color_boost = spec["color_boost"]
    wear_bias = spec["wear_bias"]
    wear_scratches = spec["wear_scratches"]
    clamp_spec_v = spec["clamp_spectrum_v"]

    sticker_metal = 1.0 if is_metallic else 0.0
    if is_metallic:
        tint_rgba = (color_tint[0], color_tint[1], color_tint[2], 1.0)
        sticker_rgb = mix_color(sticker_rgb, tint_rgba, 1.0, blend="MULTIPLY")
        if color_boost > 1.01:
            boost_n = nodes.new("ShaderNodeVectorMath")
            boost_n.operation = "SCALE"
            links.new(sticker_rgb, boost_n.inputs[0])
            boost_n.inputs["Scale"].default_value = min(color_boost, 1.25)
            sticker_rgb = boost_n.outputs["Vector"]

    # 6. Holographic SFX (view-dependent Incoming . Normal -> g_tHoloSpectrum)
    if is_holo and sfx_mask_img is not None and holo_spec_img is not None:
        sfx_tex = nodes.new("ShaderNodeTexImage")
        sfx_tex.image = sfx_mask_img
        sfx_tex.extension = "EXTEND"
        links.new(sticker_uv, sfx_tex.inputs["Vector"])
        sfx_sep = nodes.new("ShaderNodeSeparateColor")
        links.new(sfx_tex.outputs["Color"], sfx_sep.inputs["Color"])
        holo_mask = sfx_sep.outputs["Red"]
        holo_phase_u = sfx_sep.outputs["Green"]
        holo_v = sfx_sep.outputs["Blue"]

        geom = nodes.new("ShaderNodeNewGeometry")
        vdotn = nodes.new("ShaderNodeVectorMath")
        vdotn.operation = "DOT_PRODUCT"
        links.new(geom.outputs["Incoming"], vdotn.inputs[0])
        links.new(geom.outputs["Normal"], vdotn.inputs[1])
        vdotl = nodes.new("ShaderNodeVectorMath")
        vdotl.operation = "DOT_PRODUCT"
        links.new(geom.outputs["Incoming"], vdotl.inputs[0])
        vdotl.inputs[1].default_value = (0.76, 0.38, 0.52)

        spec_u = math_node("FRACT", math_node("ADD", holo_phase_u, math_node("ADD", vdotn.outputs["Value"], vdotl.outputs["Value"])))
        spec_v = math_node("ADD", holo_v, 0.0, clamp=clamp_spec_v)
        spec_v_bl = math_node("SUBTRACT", 1.0, spec_v)
        spec_comb = nodes.new("ShaderNodeCombineXYZ")
        links.new(spec_u, spec_comb.inputs["X"])
        links.new(spec_v_bl, spec_comb.inputs["Y"])

        spec_tex = nodes.new("ShaderNodeTexImage")
        spec_tex.image = holo_spec_img
        spec_tex.extension = "REPEAT"
        links.new(spec_comb.outputs["Vector"], spec_tex.inputs["Vector"])

        holo_hsv = nodes.new("ShaderNodeHueSaturation")
        holo_hsv.inputs["Saturation"].default_value = 1.15
        holo_hsv.inputs["Value"].default_value = 0.78
        links.new(spec_tex.outputs["Color"], holo_hsv.inputs["Color"])

        sticker_rgb = mix_color(sticker_rgb, holo_hsv.outputs["Color"], holo_mask)
        if is_metallic:
            sticker_metal = math_node("MULTIPLY", sticker_metal, math_node("SUBTRACT", 1.0, holo_mask))

    # 7. Exact CS2 Scrape / Wear Math with Paper Backing Reveal
    wear_in = gin.outputs["Wear"]
    has_wear = math_node("GREATER_THAN", wear_in, 0.0001)

    scr_tex = nodes.new("ShaderNodeTexImage")
    scr_tex.image = scratches_img
    scr_tex.extension = "EXTEND"
    links.new(sticker_uv, scr_tex.inputs["Vector"])
    scr_sep = nodes.new("ShaderNodeSeparateColor")
    links.new(scr_tex.outputs["Color"], scr_sep.inputs["Color"])

    scr_min = math_node("MINIMUM", wear_scratches, scr_sep.outputs["Red"])
    scr_inv = math_node("SUBTRACT", 1.0, scr_min)
    scr_mask = mix_float(scr_inv, math_node("MULTIPLY", scr_inv, 0.5), wear_in)

    inv_ss = math_node("SUBTRACT", 1.0, map_smoothstep(wear_in, 0.85, 1.0))
    cav_delta = math_node("MULTIPLY", math_node("MULTIPLY", math_node("SUBTRACT", sticker_cavity, 0.5), 2.0), inv_ss)
    eff_wear = math_node("ADD", mix_float(wear_in, math_node("ADD", wear_in, cav_delta), wear_in), 0.0, clamp=True)

    mask_remap = math_node("MULTIPLY", math_node("SUBTRACT", wear_mask, 0.078431375), 1.08510637, clamp=True)
    bias_x = float(wear_bias[0])
    bias_y = float(wear_bias[1])
    mask_pow = math_node("POWER", mask_remap, bias_x * bias_x)
    wear_thresh = math_node("SUBTRACT", eff_wear, mask_pow, clamp=True)

    rgb_worn = sticker_rgb
    if paper_backing:
        paper_thresh = math_node("ADD", math_node("MULTIPLY", wear_thresh, 2.0, clamp=True), math_node("MULTIPLY", bias_y, eff_wear))
        paper_hi = math_node("ADD", paper_thresh, 0.1)
        paper_fac = map_smoothstep(scr_mask, paper_thresh, paper_hi)
        paper_lum = mix_float(0.7, math_node("ADD", 0.2, math_node("MULTIPLY", 0.4, scr_mask)), eff_wear)
        paper_col_n = nodes.new("ShaderNodeCombineColor")
        links.new(paper_lum, paper_col_n.inputs["Red"])
        links.new(paper_lum, paper_col_n.inputs["Green"])
        links.new(paper_lum, paper_col_n.inputs["Blue"])
        rgb_worn = mix_color(paper_col_n.outputs["Color"], sticker_rgb, paper_fac)

    if paper_backing:
        peel_bias = 0.5
    else:
        peel_bias = ((bias_y * (0.5 if bias_y > 0.0 else 0.25)) + 0.5) * 0.5
    peel_thresh = math_node("SUBTRACT", math_node("MULTIPLY", wear_thresh, 1.0 + peel_bias), peel_bias, clamp=True)
    peel_hi = math_node("ADD", peel_thresh, 0.1)
    peel_alpha = map_smoothstep(scr_mask, peel_thresh, peel_hi)

    scr_rgb_n = nodes.new("ShaderNodeCombineColor")
    links.new(scr_mask, scr_rgb_n.inputs["Red"])
    links.new(scr_mask, scr_rgb_n.inputs["Green"])
    links.new(scr_mask, scr_rgb_n.inputs["Blue"])
    rgb_darkened = mix_color(
        rgb_worn,
        mix_color(rgb_worn, scr_rgb_n.outputs["Color"], 1.0, blend="MULTIPLY"),
        math_node("MULTIPLY", eff_wear, 0.3),
    )

    final_rgb = mix_color(sticker_rgb, rgb_darkened, has_wear)
    final_alpha = math_node("MULTIPLY", base_alpha, mix_float(1.0, peel_alpha, has_wear))

    if (not is_metallic) and (not preserve_rough):
        rough_thresh = map_smoothstep(eff_wear, 0.0, 0.35)
        is_scratched_rough = math_node("LESS_THAN", scr_mask, rough_thresh)
        worn_rough = mix_float(sticker_rough, 0.8, is_scratched_rough)
        final_rough = mix_float(sticker_rough, worn_rough, has_wear)
    else:
        final_rough = sticker_rough

    # 8. Composite onto weapon base material sockets
    out_color = mix_color(gin.outputs["Base Color"], final_rgb, final_alpha)
    out_rough = mix_float(gin.outputs["Base Roughness"], final_rough, final_alpha)
    out_metal = mix_float(gin.outputs["Base Metallic"], sticker_metal, final_alpha)

    nrm_mix = nodes.new("ShaderNodeMix")
    nrm_mix.data_type = "VECTOR"
    nrm_mix.clamp_factor = True
    links.new(final_alpha, nrm_mix.inputs["Factor"])
    links.new(gin.outputs["Base Normal"], nrm_mix.inputs["A"])
    links.new(sticker_normal, nrm_mix.inputs["B"])
    nrm_norm = nodes.new("ShaderNodeVectorMath")
    nrm_norm.operation = "NORMALIZE"
    links.new(nrm_mix.outputs["Result"], nrm_norm.inputs[0])

    out_alpha = math_node("MAXIMUM", gin.outputs["Base Alpha"], final_alpha)

    links.new(out_color, gout.inputs["Color"])
    links.new(out_rough, gout.inputs["Roughness"])
    links.new(out_metal, gout.inputs["Metallic"])
    links.new(nrm_norm.outputs["Vector"], gout.inputs["Normal"])
    links.new(out_alpha, gout.inputs["Alpha"])
    return ng


def _rebuild_body_stickers(
    session: Session,
    target: bpy.types.Object,
    items: list[AppliedSticker],
    *,
    remap: bool = False,
) -> list[AppliedSticker]:
    """Strip any existing sticker layers on target and rebuild shader layers for `items`."""
    strip_sticker_materials(target)
    if not items:
        return []

    body, body_kind = _active_body(target)
    if body is None:
        raise RuntimeError("No weapon mesh found for stickers.")
    main_slot, gap_slot, body_vmat = _find_body_slots(body)
    if main_slot is None or not body_vmat:
        raise RuntimeError(f"{body.name} has no material stickers can be applied to.")

    defaults = stickers_mod.parse_body_vmat_slots(session.material(body_vmat))
    model_path = _weapon_model_path(session, target)
    if model_path:
        markup = session.sticker_markup(model_path)
        defaults = stickers_mod.merge_body_slots(defaults, markup.get(body_kind))

    if remap:
        items = stickers_mod.remap_stickers_for_body(items, defaults)

    wep_inputs_img = (
        _load_image_packed(session.texture(defaults.weapon_inputs, raw=True), colorspace="Non-Color", channel_packed=True)
        if defaults.weapon_inputs
        else None
    )
    scratches_img = _load_image_packed(session.texture(SCRATCHES_VTEX, raw=True), colorspace="Non-Color", channel_packed=True)

    uv0_name = _uv0_name(body)
    uv1_name = _uv1_name(body)

    prepared = []
    updated_items: list[AppliedSticker] = []
    for item in sorted(items, key=lambda s: s.slot):
        vmat_path = f"stickers/{item.material}.vmat"
        spec = stickers_mod.sticker_material_spec(session.material(vmat_path))
        if not spec["albedo"]:
            raise RuntimeError(f"Sticker '{item.label or item.material}' has no albedo texture.")
        alb_img = _load_image_packed(session.texture(spec["albedo"], raw=False), colorspace="sRGB", channel_packed=True)
        nrm_img = _load_image_packed(session.texture(spec["normal_roughness"], raw=False), colorspace="Non-Color", channel_packed=True)
        sfx_img = None
        holo_img = None
        if spec["is_holo"]:
            sfx_img = _load_image_packed(session.texture(spec["sfx_mask"], raw=True), colorspace="Non-Color", channel_packed=True)
            holo_img = _load_image_packed(session.texture(spec["holo_spectrum"], raw=False), colorspace="sRGB", channel_packed=True)

        slot_def = defaults.slots.get(item.slot, StickerSlotDefault(item.slot, _FALLBACK_OFFSET, _FALLBACK_SCALE, 0.0))
        off = item.offset if item.offset is not None else (slot_def.offset if slot_def.scale > 0.0 else _FALLBACK_OFFSET)
        sc = item.scale if item.scale is not None else (slot_def.scale if slot_def.scale > 0.0 else _FALLBACK_SCALE)
        rot = (item.rotation_deg / 360.0) if item.rotation_deg is not None else slot_def.rotation_turns
        schema = item.schema or slot_def.schema

        norm_item = AppliedSticker(
            slot=item.slot,
            material=item.material,
            kit_name=item.kit_name,
            label=item.label,
            schema=schema,
            wear=item.wear,
            offset=item.offset,
            scale=item.scale,
            rotation_deg=item.rotation_deg,
        )
        updated_items.append(norm_item)
        prepared.append((norm_item, spec, alb_img, nrm_img, sfx_img, holo_img, off, sc, rot))

    slot_targets = [(main_slot, False)]
    if gap_slot is not None:
        slot_targets.append((gap_slot, True))

    for mat_slot, is_gap in slot_targets:
        base_mat = mat_slot.material
        wrapped = base_mat.copy()
        wrapped.name = f"{base_mat.name}.stickers"
        wrapped[STICKER_BASE_MATERIAL_PROP] = base_mat
        mat_slot.material = wrapped

        tree = wrapped.node_tree
        bsdf = _principled(wrapped)
        if tree is None or bsdf is None:
            continue

        def upstream(sock_name: str):
            s = bsdf.inputs[sock_name]
            return s.links[0].from_socket if s.links else None

        cur_color = upstream("Base Color")
        cur_rough = upstream("Roughness")
        cur_metal = upstream("Metallic")
        cur_normal = upstream("Normal")
        cur_alpha = None if is_gap else upstream("Alpha")

        if is_gap:
            for link in list(bsdf.inputs["Alpha"].links):
                tree.links.remove(link)
            if hasattr(wrapped, "surface_render_method"):
                wrapped.surface_render_method = "DITHERED"

        base_x = bsdf.location.x - 200 * len(prepared) - 100
        for idx_p, (item, spec, alb_img, nrm_img, sfx_img, holo_img, off, sc, rot) in enumerate(prepared):
            ng = build_sticker_layer_nodegroup(
                name=f"{STICKER_GROUP_PREFIX}_{body.name}_S{item.slot}_{'gap' if is_gap else 'body'}",
                spec=spec,
                albedo_img=alb_img,
                nrm_rough_img=nrm_img,
                scratches_img=scratches_img,
                wep_inputs_img=wep_inputs_img,
                sfx_mask_img=sfx_img,
                holo_spec_img=holo_img,
                offset=off,
                scale=sc,
                rotation_turns=rot,
                wear=item.wear,
                uv0_name=uv0_name,
                uv1_name=uv1_name,
                is_gap_mat=is_gap,
            )
            gnode = tree.nodes.new("ShaderNodeGroup")
            gnode.node_tree = ng
            gnode.label = f"Sticker Slot {item.slot + 1}: {item.label or item.material.rsplit('/', 1)[-1]}"
            gnode.location = (base_x + idx_p * 200, bsdf.location.y)
            gnode[STICKER_SLOT_PROP] = item.slot

            if cur_color is not None:
                tree.links.new(cur_color, gnode.inputs["Base Color"])
            else:
                gnode.inputs["Base Color"].default_value = (0.05, 0.05, 0.05, 1.0)
            if cur_rough is not None:
                tree.links.new(cur_rough, gnode.inputs["Base Roughness"])
            else:
                gnode.inputs["Base Roughness"].default_value = float(bsdf.inputs["Roughness"].default_value)
            if cur_metal is not None:
                tree.links.new(cur_metal, gnode.inputs["Base Metallic"])
            else:
                gnode.inputs["Base Metallic"].default_value = float(bsdf.inputs["Metallic"].default_value)
            if cur_normal is not None:
                tree.links.new(cur_normal, gnode.inputs["Base Normal"])
            if cur_alpha is not None:
                tree.links.new(cur_alpha, gnode.inputs["Base Alpha"])
            else:
                gnode.inputs["Base Alpha"].default_value = 0.0 if is_gap else 1.0

            cur_color = gnode.outputs["Color"]
            cur_rough = gnode.outputs["Roughness"]
            cur_metal = gnode.outputs["Metallic"]
            cur_normal = gnode.outputs["Normal"]
            cur_alpha = gnode.outputs["Alpha"]

        for sname in ("Base Color", "Roughness", "Metallic", "Normal"):
            for link in list(bsdf.inputs[sname].links):
                tree.links.remove(link)
        tree.links.new(cur_color, bsdf.inputs["Base Color"])
        tree.links.new(cur_rough, bsdf.inputs["Roughness"])
        tree.links.new(cur_metal, bsdf.inputs["Metallic"])
        tree.links.new(cur_normal, bsdf.inputs["Normal"])
        if is_gap and cur_alpha is not None:
            tree.links.new(cur_alpha, bsdf.inputs["Alpha"])

    return updated_items


def apply_sticker(
    session: Session,
    target: bpy.types.Object,
    kit: StickerKit,
    slot: int,
    *,
    wear: float = 0.0,
    offset: tuple[float, float] | None = None,
    scale: float | None = None,
    rotation_deg: float | None = None,
) -> AppliedSticker:
    """Apply or replace a sticker in `slot` (0..4) on a held or standalone weapon."""
    if not (0 <= slot < MAX_SLOTS):
        raise RuntimeError(f"Sticker slot must be between 1 and {MAX_SLOTS}.")
    existing = [s for s in applied_stickers(target) if s.slot != slot]
    new_item = AppliedSticker(
        slot=slot,
        material=kit.material,
        kit_name=kit.name,
        label=kit.label,
        wear=max(0.0, min(1.0, float(wear))),
        offset=offset,
        scale=scale,
        rotation_deg=rotation_deg,
    )
    existing.append(new_item)
    rebuilt = _rebuild_body_stickers(session, target, existing, remap=False)
    _store_applied(target, rebuilt)
    return next(s for s in rebuilt if s.slot == slot)


def remove_sticker(session: Session, target: bpy.types.Object, slot: int) -> None:
    """Remove the sticker in `slot` (0..4) while keeping all other slots intact."""
    remaining = [s for s in applied_stickers(target) if s.slot != slot]
    if not remaining:
        clear_stickers(target)
        return
    rebuilt = _rebuild_body_stickers(session, target, remaining, remap=False)
    _store_applied(target, rebuilt)


def clear_stickers(target: bpy.types.Object) -> None:
    """Remove all stickers from a held or standalone weapon."""
    strip_sticker_materials(target)
    _store_applied(target, [])


def reapply_from_props(session: Session, target: bpy.types.Object, *, remap: bool = True) -> bool:
    """Re-attach stickers stored on the weapon root onto the currently active weapon body/material."""
    items = applied_stickers(target)
    if not items:
        return False
    rebuilt = _rebuild_body_stickers(session, target, items, remap=remap)
    _store_applied(target, rebuilt)
    return True


def update_slot_live(
    target: bpy.types.Object,
    slot: int,
    *,
    wear: float | None = None,
    offset: tuple[float, float] | None = None,
    scale: float | None = None,
    rotation_deg: float | None = None,
) -> bool:
    """Update a sticker slot's shader inputs live (<1 ms, no rebuild) and persist to custom props."""
    body, _kind = _active_body(target)
    if body is None:
        return False

    updated_node = False
    for mat_slot in body.material_slots:
        mat = mat_slot.material
        if mat is None or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "GROUP" and node.get(STICKER_SLOT_PROP) == slot:
                if wear is not None and "Wear" in node.inputs:
                    node.inputs["Wear"].default_value = max(0.0, min(1.0, float(wear)))
                if offset is not None:
                    if "Offset X" in node.inputs:
                        node.inputs["Offset X"].default_value = float(offset[0])
                    if "Offset Y" in node.inputs:
                        node.inputs["Offset Y"].default_value = float(offset[1])
                if scale is not None and "Scale" in node.inputs:
                    node.inputs["Scale"].default_value = max(0.5, float(scale))
                if rotation_deg is not None and "Rotation Turns" in node.inputs:
                    node.inputs["Rotation Turns"].default_value = float(rotation_deg) / 360.0
                updated_node = True

    if not updated_node:
        return False

    items = applied_stickers(target)
    new_items: list[AppliedSticker] = []
    for item in items:
        if item.slot == slot:
            new_items.append(
                AppliedSticker(
                    slot=item.slot,
                    material=item.material,
                    kit_name=item.kit_name,
                    label=item.label,
                    schema=item.schema,
                    wear=max(0.0, min(1.0, float(wear))) if wear is not None else item.wear,
                    offset=offset if offset is not None else item.offset,
                    scale=float(scale) if scale is not None else item.scale,
                    rotation_deg=float(rotation_deg) if rotation_deg is not None else item.rotation_deg,
                )
            )
        else:
            new_items.append(item)
    _store_applied(target, new_items)
    return True


def reset_slot_transform(session: Session, target: bpy.types.Object, slot: int) -> bool:
    """Reset a sticker slot's Offset/Scale/Rotation back to the active body's default."""
    defaults = body_defaults(session, target)
    slot_def = defaults.slots.get(slot) if defaults is not None else None
    off = slot_def.offset if (slot_def is not None and slot_def.scale > 0.0) else _FALLBACK_OFFSET
    sc = slot_def.scale if (slot_def is not None and slot_def.scale > 0.0) else _FALLBACK_SCALE
    rot_deg = (slot_def.rotation_turns * 360.0) if slot_def is not None else 0.0

    ok = update_slot_live(target, slot, offset=off, scale=sc, rotation_deg=rot_deg)
    if not ok:
        return False
    # Clear custom overrides in stored JSON so legacy <-> HD swaps follow body defaults again.
    items = applied_stickers(target)
    cleaned = [
        AppliedSticker(
            slot=s.slot,
            material=s.material,
            kit_name=s.kit_name,
            label=s.label,
            schema=s.schema,
            wear=s.wear,
            offset=None,
            scale=None,
            rotation_deg=None,
        )
        if s.slot == slot
        else s
        for s in items
    ]
    _store_applied(target, cleaned)
    return True
