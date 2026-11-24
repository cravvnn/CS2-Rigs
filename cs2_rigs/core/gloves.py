"""Glove finishes: which glove model a finish is for, and the textures it paints. No Blender imports here.

Gloves use the game's `csgo_customglove` compositing shader, which has two
generations controlled by `F_BACKWARDS_COMPATIBILITY`:

- Generation 1 (`F_BACKWARDS_COMPATIBILITY = 0`, 72 older finishes): a layer mask
  blends four textile layers (`g_tDetail1..4`, `g_tGrunge1..4`, detail/damage
  normals) and stores a 3-bit tint index in alpha that picks from eight sRGB
  tints (`g_vColorTint1..8`) and an optional pattern (`g_tPattern`).
- Generation 2 (`F_BACKWARDS_COMPATIBILITY = 1`, 22 newer finishes): a layer id
  texture blends four height-blended surface/substrate layers with colour
  correction matrices, burnishing, damage bevels, grime, tint-id palettes and
  optional dyed or painted patterns.
"""

from __future__ import annotations

import math
import os

import numpy as np

from . import composite
from .composite import fit, linear_to_srgb, luma, mix, sample, saturate, smoothstep, srgb_to_linear

VERSION = 2  # raise when results change, so textures composed earlier are not reused
SEPARATOR = " | "
RECIPE_FOLDER = "gloves/paints/"
LAYERS = 4
TINTS = 8

# 9-tap Gaussian kernel from csgo_customglove (Source1 layout) for tint-mask anti-aliasing.
_TINT_KERNEL = (
    (0.0, 0.0, 0.3197279),
    (-1.0, -1.0, 0.0510204),
    (-1.0, 0.0, 0.1190476),
    (-1.0, 1.0, 0.0510204),
    (0.0, -1.0, 0.1190476),
    (0.0, 1.0, 0.1190476),
    (1.0, -1.0, 0.0510204),
    (1.0, 0.0, 0.1190476),
    (1.0, 1.0, 0.0510204),
)
_CORNER_OFFSETS = ((-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0))
_GRID_3X3 = (
    (-1.0, -1.0), (0.0, -1.0), (1.0, -1.0),
    (-1.0, 0.0),  (0.0, 0.0),  (1.0, 0.0),
    (-1.0, 1.0),  (0.0, 1.0),  (1.0, 1.0),
)
_TEXTILE_LEVELS = np.array((0.045, -1.4427 * math.log(0.4), 1.0), dtype=np.float32)
_METALLIC_TEXTILE_LEVELS = np.array((0.08, -1.4427 * math.log(0.4), 0.92), dtype=np.float32)
_DEFAULT_TINTS = (
    (0.15, 0.11, 0.08),
    (0.23, 0.16, 0.26),
    (0.16, 0.16, 0.28),
    (0.20, 0.30, 0.30),
    (0.09, 0.16, 0.10),
    (0.29, 0.26, 0.11),
    (0.32, 0.22, 0.12),
    (0.21, 0.12, 0.09),
)
_LUMA_30_59_11 = np.array((0.30, 0.59, 0.11), dtype=np.float32)
_GRAY_AXIS = np.float32(0.577)

# item name in items_game.txt -> (name shown, model that wears the finish)
GLOVES = {
    "studded_bloodhound_gloves": ("Bloodhound Gloves", "agents/models/shared/arms/glove_bloodhound/glove_bloodhound.vmdl_c"),
    "studded_brokenfang_gloves": ("Broken Fang Gloves", "agents/models/shared/arms/glove_brokenfang/glove_brokenfang.vmdl_c"),
    "studded_hydra_gloves": ("Hydra Gloves", "agents/models/shared/arms/glove_hydra/glove_hydra.vmdl_c"),
    "leather_handwraps": ("Hand Wraps", "agents/models/shared/arms/glove_handwrap/glove_handwrap.vmdl_c"),
    "motorcycle_gloves": ("Moto Gloves", "agents/models/shared/arms/glove_motorcycle/glove_motorcycle.vmdl_c"),
    "slick_gloves": ("Driver Gloves", "agents/models/shared/arms/glove_slick/glove_slick.vmdl_c"),
    "specialist_gloves": ("Specialist Gloves", "agents/models/shared/arms/glove_specialist/glove_specialist.vmdl_c"),
    "sporty_gloves": ("Sport Gloves", "agents/models/shared/arms/glove_sporty/glove_sporty.vmdl_c"),
}


def glove_item(kit) -> str:
    """The glove a finish is offered on, or '' when it is not a glove finish."""
    return next((item for item in kit.weapons if item in GLOVES), "")


def is_glove_kit(kit) -> bool:
    return bool(glove_item(kit))


def glove_kits(kits) -> list:
    """Glove finishes, sorted by glove and then by name."""
    found = [kit for kit in kits if is_glove_kit(kit)]
    return sorted(found, key=lambda kit: (GLOVES[glove_item(kit)][0], kit.label, kit.id))


def label(kit) -> str:
    """'Sport Gloves | Vice'."""
    return f"{GLOVES[glove_item(kit)][0]}{SEPARATOR}{kit.label}"


def model_path(kit) -> str:
    return GLOVES[glove_item(kit)][1]


def recipe_for(kit, recipes) -> str | None:
    """The recipe of a glove finish in `recipes`, or None when the game has none for it.

    Newer finishes name their recipe. Older ones have one next to their paint
    material, called after the finish.
    """
    named = kit.fields.get("composite_material_path", "")
    if named:
        return named if named in recipes else None
    path = f"{RECIPE_FOLDER}{kit.name}.vcompmat"
    return path if path in recipes else None


def is_left(material_name: str) -> bool:
    """Whether a glove material is for the left hand, going by the game's file name ('..._left.vmat')."""
    return os.path.basename(material_name).removesuffix(".vmat").endswith("_left")


def attributes(left: bool) -> dict:
    """What the game's glove materials say about their hand: the left one has 'left', the right one a seed offset."""
    return {"left": 1} if left else {"seed_offset": 1}


def generation(values: dict) -> int:
    """1 for Source1-layout finishes (`g_tLayerMask`), 2 for Source2 height-blended finishes (`g_tLayerId`)."""
    return 2 if "g_tLayerId" in values else 1


def _uses_pattern_gen2(values: dict) -> bool:
    if "F_PATTERN" in values:
        return bool(values["F_PATTERN"])
    return any(values.get(f"g_bId{i}Pattern") for i in range(1, TINTS + 1))


def texture_names(values: dict) -> list[str]:
    """The textures of a resolved finish that `compose` reads, by shader name."""
    if generation(values) == 2:
        names = ["g_tLayerId", "g_tTintId", "g_tObjectProperties", "g_tNormal"]
        names += [
            f"g_t{kind}{i}"
            for i in range(1, LAYERS + 1)
            for kind in ("Surface", "SurfaceNormal", "SurfaceProperties", "Substrate", "SubstrateNormal", "SubstrateProperties", "Damage", "Grime")
        ]
        if _uses_pattern_gen2(values):
            names.append("g_tPattern")
            if values.get("F_PATTERN_PAINT"):
                names.append("g_tPatternProperties")
    else:
        names = ["g_tLayerMask", "g_tSurface", "g_tNoise", "g_tNormal"]
        names += [
            f"g_t{kind}{i}"
            for i in range(1, LAYERS + 1)
            for kind in ("Detail", "Grunge", "DetailNormal", "DamageNormal")
        ]
        if values.get("F_PATTERN"):
            names.append("g_tPattern")
    return [name for name in names if values.get(name)]


def raw_texture_names(values: dict) -> set[str]:
    """Shader texture names that must be exported with `--texture_decode_flags none`.

    Generation-2 surface and substrate normal maps use `HemiOctAnisoRoughness`
    (R=roughX, G=octY, B=roughY, A=octW), which Source2Viewer's automatic
    decoder misreads as `HemiOctIsoRoughness` (RG normal + B roughness).
    """
    if generation(values) != 2:
        return set()
    return {f"g_t{kind}{i}" for i in range(1, LAYERS + 1) for kind in ("SurfaceNormal", "SubstrateNormal") if values.get(f"g_t{kind}{i}")}


def _number(params, name, default=0.0) -> float:
    value = params.get(name, default)
    if isinstance(value, (tuple, list)):
        value = value[0] if value else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _vector(params, name, default=(1.0, 1.0, 1.0)):
    value = params.get(name)
    if not isinstance(value, (tuple, list)) or len(value) < len(default):
        return np.asarray(default, dtype=np.float32)
    return np.asarray(value[: len(default)], dtype=np.float32)


def _with_alpha(image):
    if image.shape[-1] == 3:
        return np.concatenate((image, np.ones_like(image[..., :1])), axis=-1)
    return image


def _linear_rgba(image):
    """Apply `SrgbRead(true)`: linearise RGB while keeping alpha untouched."""
    image = _with_alpha(image)
    return np.concatenate((srgb_to_linear(image[..., :3]), image[..., 3:4].astype(np.float32)), axis=-1)


def _point_sample(image, u, v):
    """Nearest-texel wrap lookup, matching `g_sPoint`."""
    height, width = image.shape[:2]
    x = np.mod(np.floor(u * width).astype(np.int64), width)
    y = np.mod(np.floor(v * height).astype(np.int64), height)
    return image[y, x]


def _normalize(v, eps: float = 1e-7):
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(length, np.float32(eps))


def _decode_normal(image):
    """Tangent-space unit normal from an auto-decoded RGB normal texture."""
    return _normalize(image[..., :3] * np.float32(2.0) - np.float32(1.0))


def _decode_hemioct_aniso(rgba):
    """Decode raw `HemiOctAnisoRoughness` into unit normal `(x, y, z)` and `(rough_x, rough_y)`."""
    ox = (rgba[..., 3] + rgba[..., 1]) - np.float32(1.0039216)
    oy = rgba[..., 3] - rgba[..., 1]
    oz = np.float32(1.0) - np.abs(ox) - np.abs(oy)
    normal = _normalize(np.stack((ox, oy, oz), axis=-1))
    rough = np.stack((rgba[..., 0], rgba[..., 2]), axis=-1)
    return normal, rough


def _mix4(a, b, c, d, mr, mg, mb):
    """Sequential four-layer blend matching `mix(mix(mix(a, b, r), c, g), d, b)`."""
    ndim = np.asarray(a).ndim
    if ndim == 1 or ndim > mr.ndim:
        mr, mg, mb = mr[..., None], mg[..., None], mb[..., None]
    return mix(mix(mix(a, b, mr), c, mg), d, mb)


def _dfdx(arr):
    return np.roll(arr, -1, axis=1) - arr


def _dfdy(arr):
    return np.roll(arr, -1, axis=0) - arr


def _xform_gen1(rotation_deg: float, scale: float, offset) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Exact Source1 glove UV transform from `csgo_customglove.vfx`."""
    angle = rotation_deg * 3.14159 / 180.0
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    half = 0.5 / (scale if scale != 0.0 else 1.0)
    v5, v6 = math.cos(-angle), math.sin(-angle)
    v7 = half * v5 - half * v6
    v8 = v7 * v6 + half * v5
    row0 = (cos_a * scale, -sin_a * scale, scale * cos_a * v7 - scale * sin_a * v8 + (float(offset[0]) - 0.5))
    row1 = (sin_a * scale, cos_a * scale, scale * sin_a * v7 + scale * cos_a * v8 + (float(offset[1]) - 0.5))
    return row0, row1


def _xform_gen2(rotation_deg: float, scale, offset, center=(0.5, 0.5)) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Exact Source2 glove UV transform from `csgo_customglove.vfx` VS expressions."""
    angle = rotation_deg * 3.1415927 / 180.0
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    sx, sy = float(scale[0]), float(scale[1])
    cx, cy = float(center[0]), float(center[1])
    ox, oy = float(offset[0]), float(offset[1])
    row0 = (cos_a * sx, -sin_a * sy, cx + ox + sin_a * cy - cos_a * cx)
    row1 = (sin_a * sx, cos_a * sy, cy + oy - (sin_a * cx + cos_a * cy))
    return row0, row1


def _apply_xform(rows, u, v):
    (a, b, c), (d, e, f) = rows
    return a * u + b * v + c, d * u + e * v + f


def _adjust_saturation(base_rgb, hue_src, amount, target_luma=None):
    """Exact saturation adjustment from `csgo_customglove` (Source1 layout)."""
    base_luma = luma(base_rgb) if target_luma is None else target_luma
    hue_luma = luma(hue_src)
    v = _normalize(hue_src * hue_luma[..., None]) - _GRAY_AXIS
    sat_hue = saturate(_normalize(v) * np.float32(2.0) + np.float32(1.0))
    sat_luma = np.maximum(luma(sat_hue), np.float32(1e-6))
    boosted = sat_hue * (base_luma / sat_luma)[..., None]
    factor = saturate(amount * np.power(np.abs(np.sum(v * _GRAY_AXIS, axis=-1)), np.float32(0.2)))
    pos = mix(base_rgb, boosted, factor[..., None])
    neg = mix(base_rgb, luma(base_rgb)[..., None], (-amount)[..., None])
    return np.where((amount > 0.0)[..., None], pos, neg)


def _luminance_rotation() -> tuple[np.ndarray, np.ndarray]:
    """Normalized Rec.709 luminance axis and row-vector rotation taking it to +Z."""
    lum = composite.LUMA / np.linalg.norm(composite.LUMA)
    axis = np.cross(lum, np.array((0.0, 0.0, 1.0), dtype=np.float32))
    sin_a = float(np.linalg.norm(axis))
    cos_a = float(lum[2])
    axis = axis / max(sin_a, 1e-7)
    x, y, z = float(axis[0]), float(axis[1]), float(axis[2])
    omc = 1.0 - cos_a
    # Row-vector axis-angle rotation (matches System.Numerics.Matrix4x4.CreateFromAxisAngle).
    rot = np.array(
        (
            (cos_a + x * x * omc, x * y * omc + z * sin_a, x * z * omc - y * sin_a, 0.0),
            (x * y * omc - z * sin_a, cos_a + y * y * omc, y * z * omc + x * sin_a, 0.0),
            (x * z * omc + y * sin_a, y * z * omc - x * sin_a, cos_a + z * z * omc, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        ),
        dtype=np.float32,
    )
    return lum.astype(np.float32), rot


_LUM_NORM, _LUM_ROT = _luminance_rotation()


def _translate4(v) -> np.ndarray:
    m = np.eye(4, dtype=np.float32)
    m[3, :3] = v
    return m


def _scale4(v) -> np.ndarray:
    m = np.eye(4, dtype=np.float32)
    m[0, 0], m[1, 1], m[2, 2] = v[0], v[1], v[2]
    return m


def matrix_color_correct2(contrast: float, saturation: float, brightness: float, pivot) -> np.ndarray:
    """Row-vector 4x4 colour correction matrix matching Source 2 `MatrixColorCorrect2`."""
    pivot = np.asarray(pivot, dtype=np.float32)[:3]
    lum_scale = _scale4(_LUM_NORM)
    inv_lum_scale = _scale4(1.0 / _LUM_NORM)
    res = _translate4(-pivot) @ _scale4((contrast, contrast, contrast)) @ _translate4(pivot)
    res = res @ _scale4((brightness, brightness, brightness))
    res = res @ lum_scale @ _LUM_ROT @ _scale4((saturation, saturation, 1.0)) @ _LUM_ROT.T @ inv_lum_scale
    return res.astype(np.float32)


def matrix_color_tint2(rgb, strength: float = 1.0) -> np.ndarray:
    """Row-vector 4x4 luminance-preserving tint matrix matching Source 2 `MatrixColorTint2`."""
    rgb = np.asarray(rgb, dtype=np.float32)[:3]
    hi, lo = float(rgb.max()), float(rgb.min())
    sat = 0.0 if hi == 0.0 else (hi - lo) / hi
    lum_scale = _scale4(_LUM_NORM)
    inv_lum_scale = _scale4(1.0 / _LUM_NORM)
    gray = (np.array((rgb[0], rgb[1], rgb[2], 1.0), dtype=np.float32) @ (lum_scale @ _LUM_ROT))[:3]
    desat_xy = 1.0 - sat
    sat_factor = 1.0 - sat * sat * strength
    res = lum_scale @ _LUM_ROT
    res = res @ _translate4((-gray[0], -gray[1], 0.0))
    res = res @ _scale4((desat_xy, desat_xy, sat_factor))
    res = res @ _translate4((gray[0], gray[1], (1.0 - sat_factor) * gray[2]))
    res = res @ _LUM_ROT.T @ inv_lum_scale
    return res.astype(np.float32)


def color_adjust_matrix(tint, contrast: float, saturation: float, brightness: float, avg_rgb) -> np.ndarray:
    """Combined row-vector colour adjust matrix `MatrixMultiply(MatrixColorTint2, MatrixColorCorrect2)`."""
    return (matrix_color_correct2(contrast, saturation, brightness, avg_rgb) @ matrix_color_tint2(tint, 1.0)).astype(np.float32)


def _apply_weighted_matrix(rgb, matrices, weights):
    """Apply a layer-weighted sum of `(4, 4)` row-vector colour matrices without allocating `(H, W, 4, 4)`."""
    out = np.zeros_like(rgb)
    for mat, w in zip(matrices, weights):
        if mat is None:
            continue
        out += (rgb @ mat[:3, :3] + mat[3, :3]) * w[..., None]
    return out


def _tint_by_luma_gen2(adjusted_rgb, tint_rgb, tint_a, metalness):
    """Exact Source2 tint-application curve from `csgo_customglove` (`F_BACKWARDS_COMPATIBILITY=1`)."""
    hi = np.maximum(tint_rgb[..., 0], np.maximum(tint_rgb[..., 1], tint_rgb[..., 2]))
    lo = np.minimum(tint_rgb[..., 0], np.minimum(tint_rgb[..., 1], tint_rgb[..., 2]))
    sat = np.where(hi == 0.0, np.float32(0.0), (hi - lo) / np.maximum(hi, np.float32(1e-7)))
    tl = luma(tint_rgb)
    cl = np.maximum(adjusted_rgb @ _LUMA_30_59_11, np.float32(0.001))
    low_lim = mix(np.float32(0.03), np.float32(0.134), metalness) / cl
    high_lim = mix(np.float32(0.90), np.float32(0.98), metalness) / cl
    luma_scale = mix(np.float32(1.0), np.minimum(np.maximum(np.float32(1.0) + np.float32(4.0) * (tl - np.float32(0.5)), low_lim), high_lim), tint_a)
    v = _normalize(tint_rgb) - _GRAY_AXIS
    sat_hue = saturate(_normalize(v) * np.float32(2.0) + np.float32(1.0))
    sat_luma = np.maximum(luma(sat_hue), np.float32(1e-6))
    hue_col = sat_hue * (tl / sat_luma)[..., None]
    hue_blend = saturate(sat * np.power(np.abs(np.sum(v * _GRAY_AXIS, axis=-1)), np.float32(0.2))) * tint_a
    tinted = mix(adjusted_rgb * luma_scale[..., None], hue_col, hue_blend[..., None])
    return np.where((tl > 0.0)[..., None], tinted, adjusted_rgb)


def _emboss_normal(height_field, strength, size: int):
    """Tangent-space height-gradient normal from `cross(dFdy(pos), dFdx(pos))`."""
    inv = np.float32(1.0 / max(size, 1))
    dh_dx = _dfdx(height_field)
    dh_dy = _dfdy(height_field)
    n = _normalize(np.stack((-dh_dx, dh_dy, np.full_like(dh_dx, inv)), axis=-1))
    base = np.zeros_like(n)
    base[..., 2] = 1.0
    return _normalize(mix(base, base + n, strength[..., None]))


def compose(values: dict, textures: dict, size: int, wear: float = 0.0) -> dict:
    """Colour, roughness, metalness, normal and occlusion for one glove of one finish.

    `values` is the resolved finish (shader values by their game names, textures
    as package paths) and `textures` maps the names `texture_names` lists to
    images. Returns `colour` (sRGB encoded), `roughness`, `metalness`,
    `occlusion`, and `normal` (tangent-space RGB in `[0, 1]`), sized `size`,
    with row 0 at the top.
    """
    wear = float(min(max(wear, 0.0), 1.0))
    centres = (np.arange(size, dtype=np.float32) + 0.5) / size
    u, v = np.meshgrid(centres, centres)
    build = _newer if generation(values) == 2 else _older
    colour, rough, metal, normal, occlusion = build(values, textures, size, wear, u, v)
    shape = (size, size)
    return {
        "colour": linear_to_srgb(np.broadcast_to(colour, shape + (3,))),
        "roughness": saturate(np.broadcast_to(rough, shape)).astype(np.float32),
        "metalness": saturate(np.broadcast_to(metal, shape)).astype(np.float32),
        "normal": saturate(np.broadcast_to(normal, shape + (3,)) * np.float32(0.5) + np.float32(0.5)).astype(np.float32),
        "occlusion": saturate(np.broadcast_to(occlusion, shape)).astype(np.float32),
    }


def _older(p, textures, size, wear, u, v):
    """Source1-layout glove shader (`csgo_customglove`, `F_BACKWARDS_COMPATIBILITY = 0`)."""
    shape = (size, size)
    raw_mask = _with_alpha(textures["g_tLayerMask"])
    mask = fit(raw_mask, size)
    mr, mg, mb = mask[..., 0], mask[..., 1], mask[..., 2]

    def per_layer(name, default=0.0):
        vals = [_number(p, f"{name}{i}", default) for i in range(1, LAYERS + 1)]
        return _mix4(np.float32(vals[0]), np.float32(vals[1]), np.float32(vals[2]), np.float32(vals[3]), mr, mg, mb)

    def per_layer_vec2(name, default=(0.0, 1.0)):
        vals = [_vector(p, f"{name}{i}", default) for i in range(1, LAYERS + 1)]
        return _mix4(vals[0], vals[1], vals[2], vals[3], mr, mg, mb)

    detail_metal = per_layer("g_fDetailMetalness", 0.0)
    dmg_levels = per_layer_vec2("g_vDamageLevels", (0.0, 1.0))
    dmg_sat = per_layer("g_fDamageSaturation", 0.0)
    dmg_edge_width = per_layer("g_fDamageNormalEdgeWidth", 1.0)
    detail_grunge = per_layer("g_fDetailGrunge", 0.0)
    grime_sat = per_layer("g_fGrimeSaturation", 0.0)
    curv_power = per_layer("g_fCurvaturePower", 1.0)
    detail_scale = per_layer("g_fDetailScale", 4.0)
    detail_rough_contrast = per_layer("g_fDetailRoughnessContrast", 0.0)
    detail_rough_bright = per_layer("g_fDetailRoughnessBrightness", 1.0)

    wear_prog = np.float32(wear ** max(_number(p, "g_fWearExponent", 1.0), 0.05))
    flip = -1.0 if p.get("g_bFlipFixup") else 1.0

    # Surface properties: R=curvature, G=AO, B=bleach mask, A=wear mask.
    if "g_tSurface" in textures:
        surface = fit(_with_alpha(textures["g_tSurface"]), size)
    else:
        surface = np.broadcast_to(np.array((0.5, 1.0, 0.0, 0.5), dtype=np.float32), shape + (4,))
    curv_raw, ao_raw, bleach_mask, wear_mask = surface[..., 0], surface[..., 1], surface[..., 2], surface[..., 3]
    curv_pow = np.power(np.maximum(curv_raw, np.float32(0.0)), curv_power)

    # Sample detail layers at blended detail UV scale and apply black-point compensation.
    du, dv = u * detail_scale, v * detail_scale
    bp = [np.float32(_number(p, f"g_fDetailBlackPoint{i}", 0.047)) for i in range(1, LAYERS + 1)]
    default_detail = np.broadcast_to(np.array((0.5, 0.5, 1.0, 1.0), dtype=np.float32), shape + (4,))
    details = []
    for i in range(1, LAYERS + 1):
        if f"g_tDetail{i}" in textures:
            d = sample(_with_alpha(textures[f"g_tDetail{i}"]), du, dv, composite.WRAP, composite.WRAP)
        else:
            d = default_detail.copy()
        d_xy = bp[i - 1] + d[..., :2] * (np.float32(1.0) - bp[i - 1])
        details.append(np.concatenate((d_xy, d[..., 2:4]), axis=-1))
    detail = _mix4(details[0], details[1], details[2], details[3], mr, mg, mb)
    # Match the compiled shader's second black-point pass using layer 1's black point.
    detail_r = bp[0] + detail[..., 0] * (np.float32(1.0) - bp[0])
    detail_g = bp[0] + detail[..., 1] * (np.float32(1.0) - bp[0])
    detail_b = detail[..., 2]
    inv_gloss = np.float32(1.0) - detail[..., 3]
    detail_rough = saturate((detail_rough_contrast * (inv_gloss * inv_gloss * np.float32(0.85) - np.float32(0.35)) + np.float32(0.5)) * detail_rough_bright)

    # Grunge layers (SrgbRead=true) and noise (SrgbRead=true).
    grunge_rot = _number(p, "g_fGrungeTexCoordRotation", 0.0)
    grunge_scale = _number(p, "g_fGrungeTexCoordScale", 2.5)
    grunge_offset = _vector(p, "g_fGrungeTexCoordOffset", (0.0, 0.0))
    grunge_xform = _xform_gen1(grunge_rot, grunge_scale, grunge_offset)
    grunge_rot_xform = _xform_gen1(-grunge_rot, 1.0, (0.0, 0.0))
    gu, gv = _apply_xform(grunge_xform, u, v)
    default_grunge = np.full(shape + (4,), 0.5, dtype=np.float32)
    grunge_lin = [
        _linear_rgba(textures[f"g_tGrunge{i}"]) if f"g_tGrunge{i}" in textures else None
        for i in range(1, LAYERS + 1)
    ]
    grunges = [
        sample(img, gu, gv, composite.WRAP, composite.WRAP) if img is not None else default_grunge
        for img in grunge_lin
    ]
    grunge = _mix4(grunges[0], grunges[1], grunges[2], grunges[3], mr, mg, mb)
    grunge_rgb, grunge_a = grunge[..., :3], grunge[..., 3]
    grunge_luma = luma(grunge_rgb)
    if "g_tNoise" in textures:
        noise = sample(_linear_rgba(textures["g_tNoise"]), u * np.float32(2.0), v * np.float32(2.0), composite.WRAP, composite.WRAP)
    else:
        noise = np.broadcast_to(np.array((0.5, 0.5, 0.5, 1.0), dtype=np.float32), shape + (4,))

    # Base, detail and damage normals.
    flat_n = np.broadcast_to(np.array((0.0, 0.0, 1.0), dtype=np.float32), shape + (3,))
    base_n = _decode_normal(fit(textures["g_tNormal"], size)) if "g_tNormal" in textures else flat_n
    detail_normals = [
        _decode_normal(sample(textures[f"g_tDetailNormal{i}"], du, dv, composite.WRAP, composite.WRAP)) if f"g_tDetailNormal{i}" in textures else flat_n
        for i in range(1, LAYERS + 1)
    ]
    detail_n = _normalize(_mix4(detail_normals[0], detail_normals[1], detail_normals[2], detail_normals[3], mr, mg, mb))
    damage_normals = [
        _decode_normal(sample(textures[f"g_tDamageNormal{i}"], du, dv, composite.WRAP, composite.WRAP)) if f"g_tDamageNormal{i}" in textures else flat_n
        for i in range(1, LAYERS + 1)
    ]
    damage_n = _normalize(_mix4(damage_normals[0], damage_normals[1], damage_normals[2], damage_normals[3], mr, mg, mb))

    # 9-tap Gaussian blur of the 3-bit tint index stored in g_tLayerMask.a.
    blur = np.float32(_number(p, "g_fColorMaskBlur", 2.0) / 1024.0)
    mask_a = raw_mask[..., 3]
    palette_srgb = [_vector(p, f"g_vColorTint{i}", _DEFAULT_TINTS[i - 1])[:3] for i in range(1, TINTS + 1)]
    palette_arr = np.asarray(palette_srgb, dtype=np.float32)
    has_pattern = bool(p.get("F_PATTERN")) and "g_tPattern" in textures
    pat_mode = int(_number(p, "g_nPatternMode", 0))
    replace_idx = min(max(int(_number(p, "g_nPatternReplaceIndex", 1)) - 1, 0), TINTS - 1)
    need_pat_mask = has_pattern and pat_mode not in (1, 3)

    base_tint_srgb = np.zeros(shape + (3,), dtype=np.float32)
    replace_weight = np.zeros(shape, dtype=np.float32) if need_pat_mask else None
    for ox, oy, kw in _TINT_KERNEL:
        sampled_a = _point_sample(mask_a, u + np.float32(ox) * blur, v + np.float32(oy) * blur)
        tid = np.clip(np.floor(sampled_a * np.float32(8.0)).astype(np.int32), 0, TINTS - 1)
        base_tint_srgb += palette_arr[tid] * np.float32(kw)
        if need_pat_mask:
            replace_weight += np.where(tid == replace_idx, np.float32(kw), np.float32(0.0))
    base_tint_lin = srgb_to_linear(base_tint_srgb)

    # Pattern layer (indexed palette or direct bitmap).
    paint_thickness = np.float32(_number(p, "g_fPaintThickness", 1.0))
    pat_rot = _number(p, "g_fPatternTexCoordRotation", 0.0)
    pat_scale = _number(p, "g_fPatternTexCoordScale", 2.5)
    pat_offset = _vector(p, "g_fPatternTexCoordOffset", (0.0, 0.0))
    pat_xform = _xform_gen1(-pat_rot if flip < 0.0 else pat_rot, pat_scale, pat_offset)
    pat_rot_xform = _xform_gen1(pat_rot if flip < 0.0 else -pat_rot, 1.0, (0.0, 0.0))
    pat_img = _with_alpha(textures["g_tPattern"]) if has_pattern else None

    if has_pattern:
        nudge = ((detail_g * np.float32(2.0) - np.float32(1.0)) * np.float32(1.0 / 1024.0)) * np.float32(_number(p, "g_fPatternDetailInfluence", 0.0))
        pu, pv = _apply_xform(pat_xform, u * np.float32(flip), v)
        pu, pv = pu + nudge, pv + nudge
        pat_mask = replace_weight if need_pat_mask else np.ones(shape, dtype=np.float32)
        pat = sample(pat_img, pu, pv, composite.WRAP, composite.WRAP)
        if pat_mode < 2:
            pr, pg, pb = pat[..., 0], pat[..., 1], pat[..., 2]
            pat_sum = pr + pg + pb
            if paint_thickness > 0.0:
                shadow_power = np.float32(max(0.01, float(paint_thickness) * 0.15))
                step = np.float32((0.5 + 0.1 * float(paint_thickness)) / max(pat_img.shape[0], 1))
                blurred = (
                    pat
                    + sample(pat_img, pu + step, pv, composite.WRAP, composite.WRAP)
                    + sample(pat_img, pu - step, pv, composite.WRAP, composite.WRAP)
                    + sample(pat_img, pu, pv + step, composite.WRAP, composite.WRAP)
                    + sample(pat_img, pu, pv - step, composite.WRAP, composite.WRAP)
                ) * np.float32(0.2)
                s_prod = (
                    mix(np.power(np.maximum(np.float32(1.0) - blurred[..., 0], np.float32(0.0)), shadow_power), np.float32(1.0), pr)
                    * mix(np.power(np.maximum(np.float32(1.0) - blurred[..., 1], np.float32(0.0)), shadow_power), np.float32(1.0), pg)
                    * mix(np.power(np.maximum(np.float32(1.0) - blurred[..., 2], np.float32(0.0)), shadow_power), np.float32(1.0), pb)
                )
                paint_shadow = np.power(np.maximum(mix(np.float32(1.0), s_prod, pat_mask), np.float32(0.0)), np.float32(0.2))
            else:
                paint_shadow = np.ones(shape, dtype=np.float32)
            paint_cover = saturate(pat_sum) * pat_mask * saturate(paint_thickness - detail_b * (wear_prog ** 4))
            paint_thick = paint_cover * np.float32(min(1.0, float(paint_thickness)))
            wear_mask_adj = wear_mask - paint_thick * np.float32(_number(p, "g_fPaintDurability", 0.5))
            detail_g_pat = mix(detail_g, np.float32(0.5), paint_cover)
            detail_n_pat = mix(detail_n, flat_n, paint_thick[..., None])
            paint_height = pat_sum * paint_thickness
            indices = [min(max(int(i), 1), TINTS) - 1 for i in p.get("g_vPatternPaletteIndices", (5, 6, 7, 8))][:4]
            while len(indices) < 4:
                indices.append(0)
            pat_srgb = mix(
                mix(mix(palette_srgb[indices[0]], palette_srgb[indices[1]], pr[..., None]), palette_srgb[indices[2]], pg[..., None]),
                palette_srgb[indices[3]],
                pb[..., None],
            )
            rough_pat = detail_rough * mix(np.float32(1.0), np.float32(_number(p, "g_fPatternRoughnessBrightness", 1.0)), paint_thick)
        else:
            wear_mask_adj = wear_mask
            detail_g_pat = detail_g
            detail_n_pat = detail_n
            paint_height = np.ones(shape, dtype=np.float32)
            paint_shadow = np.ones(shape, dtype=np.float32)
            pat_srgb = pat[..., :3]
            rough_pat = detail_rough
        metal_base = mix(detail_metal, np.float32(_number(p, "g_fPatternMetalness", 0.0)), pat_mask)
        pat_lin = srgb_to_linear(pat_srgb)
    else:
        pu = pv = None
        metal_base = detail_metal
        wear_mask_adj = wear_mask
        detail_g_pat = detail_g
        detail_n_pat = detail_n
        pat_mask = np.ones(shape, dtype=np.float32)
        paint_height = np.ones(shape, dtype=np.float32)
        paint_shadow = np.ones(shape, dtype=np.float32)
        pat_lin = np.zeros(shape + (3,), dtype=np.float32)
        rough_pat = detail_rough

    # Wear and damage masks.
    wear_curv_base = curv_pow * wear_mask_adj
    wear_curv_boost = per_layer("g_fCurvatureWearBoost", 0.0) * curv_pow
    wear_drive = saturate(wear_curv_base * grunge_a + wear_curv_boost)
    dmg_lo, dmg_hi = dmg_levels[..., 0], dmg_levels[..., 1]
    damage = smoothstep(dmg_lo, dmg_hi, wear_drive * wear_prog)
    inv_damage = np.float32(1.0) - damage
    damage_detail = saturate(damage + saturate(smoothstep(dmg_lo, dmg_hi, wear_drive * (wear_prog * np.float32(4.0)))) * inv_damage * detail_b)

    tint_lin = mix(base_tint_lin, mix(pat_lin, base_tint_lin, damage_detail[..., None]), pat_mask[..., None]) if has_pattern else base_tint_lin
    tint_luma = luma(tint_lin)

    # Micro-detail edge sharpening and luminance-matched tinting.
    a_curv = detail_g_pat * curv_pow
    adx, ady = np.abs(_dfdx(a_curv)), np.abs(_dfdy(a_curv))
    b_curv = detail_r * curv_pow
    bdx, bdy = np.abs(_dfdx(b_curv)), np.abs(_dfdy(b_curv))
    surf_sum = curv_raw + ao_raw + bleach_mask
    sdx, sdy = saturate(np.abs(_dfdx(surf_sum))), saturate(np.abs(_dfdy(surf_sum)))
    sharp_a = (np.float32(1.0) - adx) * (np.float32(1.0) - ady) * saturate(adx + ady)
    sharp_b = (np.float32(1.0) - bdx) * (np.float32(1.0) - bdy) * saturate(bdx + bdy)
    detail_val = np.minimum(
        np.float32(1.0),
        mix(detail_g_pat, detail_r, damage_detail) + mix(sharp_a, sharp_b, damage) * (np.float32(1.0) - sdx) * (np.float32(1.0) - sdy),
    )

    tint_boost = np.maximum(tint_lin, tint_lin * (np.float32(1.0) + curv_pow * np.float32(0.5))[..., None])
    tint_norm = _normalize(np.maximum(tint_boost, np.float32(0.001)))
    tint_max = np.maximum(tint_boost[..., 0], np.maximum(tint_boost[..., 1], tint_boost[..., 2]))
    albedo = saturate(tint_norm * np.minimum(detail_val / np.maximum(luma(tint_norm), np.float32(1e-6)), np.float32(3.0) * detail_val * tint_max)[..., None])

    # Bleaching, grunge, damage darkening/saturation, crevice grime and noise variation.
    wear_bleach = per_layer("g_fWearBleaching", 0.25)
    dmg_bleach = per_layer("g_fDamageBleaching", 0.0)
    bleach = mix(wear_prog * bleach_mask * wear_bleach * mix(detail_b, np.float32(0.0), saturate(paint_height - np.float32(1.0))), dmg_bleach, damage)
    grime_col = _adjust_saturation(grunge_rgb, grunge_rgb, grime_sat, target_luma=grunge_luma)
    albedo_grunge = albedo * grunge_rgb * np.float32(2.0)
    grime_bright = per_layer("g_fGrimeBrightness", 0.0)
    albedo_mixed = mix(
        mix(albedo, albedo_grunge, (detail_grunge * (np.float32(1.0) + wear_prog * detail_grunge) * inv_damage)[..., None]),
        grime_col * ((np.float32(1.0) + grime_bright) * detail_val)[..., None],
        bleach[..., None],
    )
    dmg_bright = per_layer("g_fDamageBrightness", 0.0)
    dmg_col = _adjust_saturation(albedo_mixed, tint_lin, dmg_sat) * (np.float32(1.0) + dmg_bright)[..., None]
    albedo_dmg = mix(albedo_mixed, dmg_col, damage[..., None])

    grunge_max = per_layer("g_fGrungeMax", 1.0)
    crevice_grunge = smoothstep(np.float32(0.45), np.float32(0.75), (np.float32(1.0) - curv_raw * ao_raw * ao_raw) * wear_prog) * (np.float32(1.0) - tint_luma * np.float32(0.5)) * grunge_max
    albedo_crev = mix(albedo_dmg, grunge_rgb * albedo_dmg, crevice_grunge[..., None])

    # Subtle noise-driven hue shift (lines 570-655 of ps_newer).
    half_luma = luma(albedo_crev) * np.float32(0.5)
    r_dom = (albedo_crev[..., 0] > albedo_crev[..., 1]) & (albedo_crev[..., 0] > albedo_crev[..., 2])
    g_dom = (~r_dom) & (albedo_crev[..., 1] > albedo_crev[..., 0]) & (albedo_crev[..., 1] > albedo_crev[..., 2])
    warm_base = np.array((0.6, 1.0, 0.0), dtype=np.float32)
    cool_base = np.array((0.2, 0.6, 1.0), dtype=np.float32)
    zxy = albedo_crev[..., (2, 0, 1)]
    zyx = albedo_crev[..., (2, 1, 0)]
    yzx = albedo_crev[..., (1, 2, 0)]
    warm_hue = np.where((r_dom | g_dom)[..., None], warm_base + zxy, warm_base)
    cool_hue = np.where(r_dom[..., None], cool_base + zyx, np.where(g_dom[..., None], cool_base + yzx, cool_base))
    noise_sat = np.power(np.maximum(np.float32(1.0) - tint_luma, np.float32(0.0)), np.float32(3.5)) * np.float32(0.3) + (
        np.linalg.norm(albedo_crev - half_luma[..., None], axis=-1) * np.float32(0.15) + np.float32(0.05)
    )
    ao8 = np.power(np.maximum(ao_raw, np.float32(0.0)), np.float32(8.0))
    cool_col = _adjust_saturation(albedo_crev, cool_hue, noise_sat, target_luma=half_luma)
    warm_col = _adjust_saturation(albedo_crev, warm_hue, noise_sat, target_luma=half_luma)
    albedo_varied = saturate(mix(mix(cool_col, warm_col, saturate(ao8 * noise[..., 0])[..., None]), albedo_crev, saturate(noise[..., 2] * (np.float32(0.5) + ao8 * np.float32(0.5)))[..., None]))

    # Damage-edge gradient normal and edge mask (4-tap corner difference on grunge.a).
    edge_grad = np.zeros(shape + (2,), dtype=np.float32)
    edge_abs = np.zeros(shape, dtype=np.float32)
    if wear > 0.0:
        edge_step = (np.float32(1.0 / 1024.0) * dmg_edge_width) * (np.float32(1.0) + paint_height)
        for cx, cy in _CORNER_OFFSETS:
            su, sv = gu + np.float32(cx) * edge_step, gv + np.float32(cy) * edge_step
            ga = [
                sample(img[..., 3:4], su, sv, composite.WRAP, composite.WRAP)[..., 0] if img is not None else np.float32(0.5)
                for img in grunge_lin
            ]
            ga_mix = _mix4(ga[0], ga[1], ga[2], ga[3], mr, mg, mb)
            diff = damage - smoothstep(dmg_lo, dmg_hi, saturate(ga_mix * wear_curv_base + wear_curv_boost) * wear_prog)
            edge_grad[..., 0] -= np.float32(cx) * diff
            edge_grad[..., 1] -= np.float32(cy) * diff
            edge_abs += np.abs(diff)
    edge_mask = edge_abs * np.float32(0.25)
    edge_vec = edge_grad * (dmg_edge_width * np.float32(0.25))[..., None]
    rot_edge_x = -(edge_vec[..., 0] * np.float32(grunge_rot_xform[0][0]) + edge_vec[..., 1] * np.float32(grunge_rot_xform[0][1]))
    rot_edge_y = edge_vec[..., 0] * np.float32(grunge_rot_xform[1][0]) + edge_vec[..., 1] * np.float32(grunge_rot_xform[1][1])

    # Paint-thickness emboss normal (4-tap corner difference on g_tPattern).
    paint_emboss = np.zeros(shape + (2,), dtype=np.float32)
    if has_pattern and pat_mode < 2 and paint_thickness > 0.0:
        p_step = (np.float32(1.0 / 1024.0) * paint_thickness) * (np.float32(1.0) - edge_mask) * np.float32(2.0)
        acc = np.zeros(shape + (2,), dtype=np.float32)
        cover = saturate(inv_damage) * pat_mask
        for cx, cy in _CORNER_OFFSETS:
            sp = sample(pat_img, pu + np.float32(cx) * p_step, pv + np.float32(cy) * p_step, composite.WRAP, composite.WRAP)
            sr, sg, sb = sp[..., 0], sp[..., 1], sp[..., 2]
            dh = paint_height - (sr + sg + sb) * paint_thickness
            overlap = np.float32(1.0) - np.minimum(np.minimum(sr, sb - np.minimum(sr, sg)), sg - sr)
            w_tap = dh * cover * overlap * np.float32(0.5)
            acc[..., 0] -= np.float32(cx) * w_tap
            acc[..., 1] -= np.float32(cy) * w_tap
        px = acc[..., 0] * np.float32(pat_rot_xform[0][0]) + acc[..., 1] * np.float32(pat_rot_xform[0][1])
        py = acc[..., 0] * np.float32(pat_rot_xform[1][0]) + acc[..., 1] * np.float32(pat_rot_xform[1][1])
        # Shader flips X by g_fFlipFixup and by -1 when u > 0 (always true on (0, 1)).
        paint_emboss[..., 0] = -px * np.float32(flip)
        paint_emboss[..., 1] = py

    # Combine normals.
    micro_n = mix(detail_n_pat, damage_n, damage[..., None])
    normal_contrast = per_layer("g_fDetailNormalContrast", 1.0)
    combined_xy = base_n[..., :2] + micro_n[..., :2] * normal_contrast[..., None] - np.stack((rot_edge_x, rot_edge_y), axis=-1) + paint_emboss
    normal_out = _normalize(np.concatenate((combined_xy, base_n[..., 2:3]), axis=-1))

    # Final metalness, roughness, occlusion and textile albedo levels.
    metal = mix(mix(metal_base, per_layer("g_fDamageMetalness", 0.0), damage), per_layer("g_fDamageEdgeMetalness", 0.0), edge_mask)
    inv_detail_r = np.float32(1.0) - detail_r
    dmg_rough = saturate(
        (per_layer("g_fDamageRoughnessContrast", 0.0) * (inv_detail_r * inv_detail_r * np.float32(0.85) - np.float32(0.35)) + np.float32(0.5))
        * per_layer("g_fDamageRoughnessBrightness", 1.0)
    )
    surf_rough = mix(np.minimum(np.float32(1.0), rough_pat), (np.float32(1.0) - grunge_luma) + per_layer("g_fGrungeRoughnessBrightness", 0.8), bleach)
    rough = mix(mix(surf_rough, dmg_rough, damage), per_layer("g_fDamageEdgeRoughness", 0.8), edge_mask)
    occlusion = ao_raw * np.power(np.maximum(micro_n[..., 2], np.float32(0.0)), np.float32(0.2)) * (np.float32(1.0) - edge_abs * np.float32(0.05)) * mix(paint_shadow, np.float32(1.0), damage)

    # Textile albedo levels clamp at worn progress.
    levels = mix(_TEXTILE_LEVELS, _METALLIC_TEXTILE_LEVELS, metal[..., None])
    hue_out = _normalize(np.maximum(albedo_varied, np.float32(0.0003)))
    ref_luma = luma(mix(albedo, albedo_grunge, detail_grunge[..., None]))
    albedo_max = np.maximum(albedo_varied[..., 0], np.maximum(albedo_varied[..., 1], albedo_varied[..., 2]))
    hue_max = np.maximum(hue_out[..., 0], np.maximum(hue_out[..., 1], hue_out[..., 2]))
    leveled_val = mix(np.minimum(levels[..., 0], ref_luma), levels[..., 2], saturate(np.power(np.maximum(albedo_max, np.float32(0.0)), levels[..., 1])))
    leveled = hue_out * (leveled_val / np.maximum(hue_max, np.float32(1e-6)))[..., None]
    colour = mix(albedo_varied, leveled, wear_prog)
    return colour, rough, metal, normal_out, occlusion


def _newer(p, textures, size, wear, u, v):
    """Source2 height-blended glove shader (`csgo_customglove`, `F_BACKWARDS_COMPATIBILITY = 1`)."""
    shape = (size, size)
    # Layer order in Source2 shader: R=layer1, G=layer2, B=layer3, remainder=layer4.
    lid = fit(textures["g_tLayerId"], size)[..., :3]
    sum_rgb = lid[..., 0] + lid[..., 1] + lid[..., 2]
    w4 = np.maximum(np.float32(0.0), np.float32(1.0) - sum_rgb)
    total = np.maximum(sum_rgb + w4, np.float32(1e-6))
    weights = [lid[..., 0] / total, lid[..., 1] / total, lid[..., 2] / total, w4 / total]

    # Tint ID anti-aliasing (3x3 edge-preserving filter on ceil(tintId.r * 7.0)).
    use_tint_id = bool(p.get("F_TINT_ID", 1)) and "g_tTintId" in textures
    if use_tint_id:
        tint_tex = textures["g_tTintId"][..., 0]
        th, tw = tint_tex.shape[:2]
        du_t, dv_t = np.float32(1.0 / tw), np.float32(1.0 / th)
        samples_id = [
            np.clip(np.ceil(_point_sample(tint_tex, u + np.float32(ox) * du_t, v + np.float32(oy) * dv_t) * np.float32(7.0)).astype(np.int32), 0, TINTS - 1)
            for ox, oy in _GRID_3X3
        ]
        pat_id_mask = np.zeros(shape, dtype=np.float32)
        acc_rgb = np.zeros(shape + (3,), dtype=np.float32)
        acc_a = np.zeros(shape, dtype=np.float32)
        for index in range(TINTS):
            g = [(s == index).astype(np.float32) for s in samples_id]
            vert = (g[4] == g[1]) & (g[4] == g[7])
            horiz = (g[4] == g[3]) & (g[4] == g[5])
            aa = np.float32(0.03125) * (g[0] + g[2] + g[6] + g[8]) + np.float32(0.09375) * (g[2] + g[3] + g[5] + g[7]) + np.float32(0.5) * g[4]
            w_id = np.where(vert | horiz, g[4], aa)
            if p.get(f"g_bId{index + 1}Pattern", 1):
                pat_id_mask += w_id
            col_srgb = _vector(p, f"g_vId{index + 1}Color", (1.0, 1.0, 1.0, 1.0))
            col_lin = srgb_to_linear(col_srgb[:3])
            ca = np.float32(col_srgb[3])
            acc_rgb += (col_lin * ca) * w_id[..., None]
            acc_a += ca * w_id
        tint_rgb = np.where((acc_a > 0.0)[..., None], acc_rgb / np.maximum(acc_a, np.float32(1e-6))[..., None], np.float32(1.0))
        tint_a = acc_a
    else:
        pat_id_mask = np.ones(shape, dtype=np.float32)
        tint_rgb = np.ones(shape + (3,), dtype=np.float32)
        tint_a = np.zeros(shape, dtype=np.float32)

    # Shared UV transforms from VS.
    dmg_xform = _xform_gen2(_number(p, "g_fDamageTexCoordRotation", 0.0), (1.0, 1.0), _vector(p, "g_vDamageTexCoordOffset", (0.0, 0.0)))
    grime_xform = _xform_gen2(_number(p, "g_fGrimeTexCoordRotation", 0.0), (1.0, 1.0), _vector(p, "g_vGrimeTexCoordOffset", (0.0, 0.0)))
    du_base, dv_base = _apply_xform(dmg_xform, u, v)
    gu_base, gv_base = _apply_xform(grime_xform, u, v)

    # Accumulate layer-weighted textures, colour matrices and scalars.
    surf_rgba = np.zeros(shape + (4,), dtype=np.float32)
    sub_rgba = np.zeros(shape + (4,), dtype=np.float32)
    surf_props = np.zeros(shape + (4,), dtype=np.float32)
    sub_props = np.zeros(shape + (4,), dtype=np.float32)
    surf_norm_raw = np.zeros(shape + (4,), dtype=np.float32)
    sub_norm_raw = np.zeros(shape + (4,), dtype=np.float32)
    dmg_val = np.zeros(shape, dtype=np.float32)
    grime_rgba = np.zeros(shape + (4,), dtype=np.float32)

    m_surf = [None] * LAYERS
    m_sub = [None] * LAYERS
    m_dmg = [None] * LAYERS
    m_surf_burn = [None] * LAYERS
    m_sub_burn = [None] * LAYERS

    scalars = {
        "sub_trans": np.zeros(shape, dtype=np.float32),
        "dmg_soft": np.zeros(shape, dtype=np.float32),
        "bevel_soft": np.zeros(shape, dtype=np.float32),
        "bevel_emboss": np.zeros(shape, dtype=np.float32),
        "bevel_rough": np.zeros(shape, dtype=np.float32),
        "bevel_aniso": np.zeros(shape, dtype=np.float32),
        "bevel_metal": np.zeros(shape, dtype=np.float32),
        "bevel_to_sub": np.zeros(shape, dtype=np.float32),
        "burn_metal": np.zeros(shape, dtype=np.float32),
        "burn_grime": np.zeros(shape, dtype=np.float32),
        "grime_rough": np.zeros(shape, dtype=np.float32),
        "burn_n_scale": np.zeros(shape, dtype=np.float32),
        "surf_burn_rough": np.zeros(shape, dtype=np.float32),
        "sub_burn_rough": np.zeros(shape, dtype=np.float32),
    }
    vec2s = {
        "dmg_mm": np.zeros(shape + (2,), dtype=np.float32),
        "surf_burn_mm": np.zeros(shape + (2,), dtype=np.float32),
        "sub_burn_mm": np.zeros(shape + (2,), dtype=np.float32),
        "surf_grime_mm": np.zeros(shape + (2,), dtype=np.float32),
        "sub_grime_mm": np.zeros(shape + (2,), dtype=np.float32),
    }
    bevel_use_tint = np.zeros(shape, dtype=np.float32)

    default_albedo = np.broadcast_to(np.array((1.0, 1.0, 1.0, 1.0), dtype=np.float32), shape + (4,))
    default_surf_props = np.broadcast_to(np.array((1.0, 0.0, 0.0, 0.8), dtype=np.float32), shape + (4,))
    default_sub_props = np.broadcast_to(np.array((1.0, 0.0, 0.0, 0.2), dtype=np.float32), shape + (4,))
    default_norm_raw = np.broadcast_to(np.array((0.5, 0.5, 0.5, 0.5), dtype=np.float32), shape + (4,))
    default_grime = np.full(shape + (3,), 0.5, dtype=np.float32)

    for i in range(1, LAYERS + 1):
        w = weights[i - 1]
        if not w.any():
            continue
        w3 = w[..., None]
        uv_scale = _vector(p, f"g_vUvScale{i}", (4.0, 4.0))
        uv_offset = _vector(p, f"g_vUvOffset{i}", (0.0, 0.0))
        lu, lv = u * uv_scale[0] + uv_offset[0], v * uv_scale[1] + uv_offset[1]

        if f"g_tSurface{i}" in textures:
            s_src = _linear_rgba(textures[f"g_tSurface{i}"])
            s_avg = s_src[..., :3].reshape(-1, 3).mean(axis=0)
            s_tex = sample(s_src, lu, lv, composite.WRAP, composite.WRAP)
        else:
            s_avg = np.ones(3, dtype=np.float32)
            s_tex = default_albedo
        if f"g_tSubstrate{i}" in textures:
            b_src = _linear_rgba(textures[f"g_tSubstrate{i}"])
            b_avg = b_src[..., :3].reshape(-1, 3).mean(axis=0)
            b_tex = sample(b_src, lu, lv, composite.WRAP, composite.WRAP)
        else:
            b_avg = s_avg
            b_tex = s_tex

        surf_rgba += s_tex * w3
        sub_rgba += b_tex * w3
        surf_props += (sample(_with_alpha(textures[f"g_tSurfaceProperties{i}"]), lu, lv, composite.WRAP, composite.WRAP) if f"g_tSurfaceProperties{i}" in textures else default_surf_props) * w3
        sub_props += (sample(_with_alpha(textures[f"g_tSubstrateProperties{i}"]), lu, lv, composite.WRAP, composite.WRAP) if f"g_tSubstrateProperties{i}" in textures else default_sub_props) * w3
        surf_norm_raw += (sample(_with_alpha(textures[f"g_tSurfaceNormal{i}"]), lu, lv, composite.WRAP, composite.WRAP) if f"g_tSurfaceNormal{i}" in textures else default_norm_raw) * w3
        sub_norm_raw += (sample(_with_alpha(textures[f"g_tSubstrateNormal{i}"]), lu, lv, composite.WRAP, composite.WRAP) if f"g_tSubstrateNormal{i}" in textures else default_norm_raw) * w3

        if f"g_tDamage{i}" in textures:
            d_scale = np.float32(_number(p, f"g_fDamageUvScale{i}", 4.0))
            dmg_val += sample(_with_alpha(textures[f"g_tDamage{i}"]), du_base * d_scale, dv_base * d_scale, composite.WRAP, composite.WRAP)[..., 0] * w
        else:
            dmg_val += np.float32(0.5) * w

        if f"g_tGrime{i}" in textures:
            g_scale = np.float32(_number(p, f"g_fGrimeUvScale{i}", 4.0))
            g_rgb = sample(_linear_rgba(textures[f"g_tGrime{i}"]), gu_base * g_scale, gv_base * g_scale, composite.WRAP, composite.WRAP)[..., :3]
        else:
            g_rgb = default_grime
        grime_rgba[..., :3] += g_rgb * w3
        grime_rgba[..., 3] += np.float32(_number(p, f"g_fGrimeTranslucency{i}", 0.5)) * w

        # Per-layer colour adjustment matrices (evaluated on material tint, contrast, saturation, brightness).
        s_tint = srgb_to_linear(_vector(p, f"g_vSurfaceColorTint{i}", (1.0, 1.0, 1.0))[:3])
        b_tint = srgb_to_linear(_vector(p, f"g_vSubstrateColorTint{i}", (1.0, 1.0, 1.0))[:3])
        d_tint = srgb_to_linear(_vector(p, f"g_vDamageBevelColorTint{i}", (1.0, 1.0, 1.0))[:3])
        u_tint = srgb_to_linear(_vector(p, f"g_vBurnishingColorTint{i}", (1.0, 1.0, 1.0))[:3])
        m_surf[i - 1] = color_adjust_matrix(s_tint, _number(p, f"g_fSurfaceColorContrast{i}", 1.0), _number(p, f"g_fSurfaceColorSaturation{i}", 1.0), _number(p, f"g_fSurfaceColorBrightness{i}", 1.0), s_avg)
        m_sub[i - 1] = color_adjust_matrix(b_tint, _number(p, f"g_fSubstrateColorContrast{i}", 1.0), _number(p, f"g_fSubstrateColorSaturation{i}", 1.0), _number(p, f"g_fSubstrateColorBrightness{i}", 1.0), b_avg)
        m_dmg[i - 1] = color_adjust_matrix(d_tint, _number(p, f"g_fDamageBevelColorContrast{i}", 1.0), _number(p, f"g_fDamageBevelColorSaturation{i}", 1.0), _number(p, f"g_fDamageBevelColorBrightness{i}", 1.0), s_avg)
        b_c, b_s, b_b = _number(p, f"g_fBurnishingColorContrast{i}", 1.0), _number(p, f"g_fBurnishingColorSaturation{i}", 1.0), _number(p, f"g_fBurnishingColorBrightness{i}", 1.0)
        m_surf_burn[i - 1] = color_adjust_matrix(u_tint, b_c, b_s, b_b, s_avg)
        m_sub_burn[i - 1] = color_adjust_matrix(u_tint, b_c, b_s, b_b, b_avg)

        h_soft = max(_number(p, f"g_fDamageHeightBlendSoftness{i}", 0.01), 0.001)
        b_soft = max(_number(p, f"g_fDamageBevelBlendSoftness{i}", 0.5) + h_soft, 0.001)
        scalars["sub_trans"] += np.float32(_number(p, f"g_fSubstrateCompositeColorTranslucency{i}", 0.5)) * w
        scalars["dmg_soft"] += np.float32(h_soft) * w
        scalars["bevel_soft"] += np.float32(b_soft) * w
        scalars["bevel_emboss"] += np.float32(_number(p, f"g_fDamageBevelEmboss{i}", 1.0)) * w
        scalars["bevel_rough"] += np.float32(_number(p, f"g_fDamageBevelRoughnessBrightness{i}", 1.0)) * w
        scalars["bevel_aniso"] += np.float32(_number(p, f"g_fDamageBevelAnisotropy{i}", 0.0)) * w
        scalars["bevel_metal"] += np.float32(_number(p, f"g_fDamageBevelMetalness{i}", 0.0)) * w
        scalars["bevel_to_sub"] += np.float32(1.0 if p.get(f"g_bDamageBevelBlendToSubstrate{i}") else 0.0) * w
        scalars["burn_metal"] += np.float32(_number(p, f"g_fBurnishingMetalness{i}", 0.0)) * w
        scalars["burn_grime"] += np.float32(_number(p, f"g_fBurnishingGrime{i}", 0.5)) * w
        scalars["grime_rough"] += np.float32(_number(p, f"g_fGrimeRoughnessBrightness{i}", 1.0)) * w
        scalars["burn_n_scale"] += np.float32(_number(p, f"g_fBurnishingNormalScale{i}", 1.0)) * w
        scalars["surf_burn_rough"] += np.float32(_number(p, f"g_fSurfaceBurnishingRoughnessBrightness{i}", 1.0)) * w
        scalars["sub_burn_rough"] += np.float32(_number(p, f"g_fSubstrateBurnishingRoughnessBrightness{i}", 1.0)) * w

        vec2s["dmg_mm"] += _vector(p, f"g_vDamageMinMax{i}", (0.0, 1.0)) * w3
        vec2s["surf_burn_mm"] += _vector(p, f"g_vSurfaceBurnishingMinMax{i}", (0.0, 1.0)) * w3
        vec2s["sub_burn_mm"] += _vector(p, f"g_vSubstrateBurnishingMinMax{i}", (0.0, 1.0)) * w3
        vec2s["surf_grime_mm"] += _vector(p, f"g_vSurfaceGrimeMinMax{i}", (0.0, 1.0)) * w3
        vec2s["sub_grime_mm"] += _vector(p, f"g_vSubstrateGrimeMinMax{i}", (0.0, 1.0)) * w3
        bevel_use_tint += np.float32(1.0 if p.get(f"g_bDamageBevelUseTintMask{i}") else 0.0) * w

    surf_n, surf_rough_xy = _decode_hemioct_aniso(surf_norm_raw)
    sub_n, sub_rough_xy = _decode_hemioct_aniso(sub_norm_raw)

    # Pattern lookup (VS transform + normal distortion).
    has_pattern = _uses_pattern_gen2(p) and "g_tPattern" in textures
    is_paint_layer = bool(p.get("F_PATTERN_PAINT")) if "F_PATTERN_PAINT" in p else (has_pattern and "F_PATTERN" not in p)
    is_puffy = bool(p.get("F_PUFFY_PAINT"))
    respect_tint_mask = bool(p.get("g_fPatternPaintRespectsTintMask"))

    if has_pattern:
        pat_scale = _vector(p, "g_vPatternTexCoordScale", (1.0, 1.0))
        pat_offset = _vector(p, "g_vPatternTexCoordOffset", (0.0, 0.0))
        pat_center = _vector(p, "g_vPatternTexCoordCenter", (0.5, 0.5))
        pat_rot = _number(p, "g_fPatternTexCoordRotation", 0.0)
        pat_xform = _xform_gen2(pat_rot, pat_scale, pat_offset, pat_center)
        u_flip = -u if p.get("g_bFlipFixup") else u
        pu_base, pv_base = _apply_xform(pat_xform, u_flip, v)
        distort = np.float32(_number(p, "g_fPatternNormalDistortion", 0.0) * 0.1) * pat_scale
        pat_img = _linear_rgba(textures["g_tPattern"])
        pu_s, pv_s = pu_base - distort[0] * surf_n[..., 0], pv_base - distort[1] * surf_n[..., 1]
        pu_b, pv_b = pu_base - distort[0] * sub_n[..., 0], pv_base - distort[1] * sub_n[..., 1]
        pat_surf = sample(pat_img, pu_s, pv_s, composite.WRAP, composite.WRAP)
        pat_sub = sample(pat_img, pu_b, pv_b, composite.WRAP, composite.WRAP)
        if "g_tPatternProperties" in textures:
            pat_props = sample(_with_alpha(textures["g_tPatternProperties"]), pu_s, pv_s, composite.WRAP, composite.WRAP)
        else:
            pat_props = np.broadcast_to(np.array((0.0, 0.5, 0.0, 1.0), dtype=np.float32), shape + (4,))
        raw_pat_a = pat_surf[..., 3]
        if is_puffy:
            thresh = np.float32(_number(p, "g_fPatternTranslucencyThreshold", 0.01))
            pat_surf = np.concatenate((pat_surf[..., :3], smoothstep(thresh, thresh + np.float32(0.04), raw_pat_a)[..., None]), axis=-1)
            puffy_height = saturate(raw_pat_a * (np.float32(1.0) + thresh) - thresh)
        else:
            puffy_height = raw_pat_a
    else:
        pat_surf = np.zeros(shape + (4,), dtype=np.float32)
        pat_sub = pat_surf
        pat_props = np.broadcast_to(np.array((0.0, 0.5, 0.0, 1.0), dtype=np.float32), shape + (4,))
        puffy_height = np.zeros(shape, dtype=np.float32)

    # Apply colour matrices and tint/dyed-pattern curves.
    surf_adj = _apply_weighted_matrix(surf_rgba[..., :3], m_surf, weights)
    sub_adj = _apply_weighted_matrix(sub_rgba[..., :3], m_sub, weights)
    dmg_adj = _apply_weighted_matrix(surf_rgba[..., :3], m_dmg, weights)
    surf_burn_col = _apply_weighted_matrix(surf_rgba[..., :3], m_surf_burn, weights)
    sub_burn_col = _apply_weighted_matrix(sub_rgba[..., :3], m_sub_burn, weights)

    surf_ao, surf_metal, surf_height = surf_props[..., 0], surf_props[..., 1], surf_props[..., 3]
    sub_ao, sub_metal, sub_height = sub_props[..., 0], sub_props[..., 1], sub_props[..., 3]
    bevel_rough_xy = surf_rough_xy * scalars["bevel_rough"][..., None]
    bevel_rough_xy = np.stack((bevel_rough_xy[..., 0], mix(bevel_rough_xy[..., 0], bevel_rough_xy[..., 1], scalars["bevel_aniso"])), axis=-1)

    if has_pattern and not is_paint_layer:
        # Dyed pattern: pattern blends into the tint before luma-preserving tinting.
        pat_cov = pat_id_mask * pat_surf[..., 3]
        no_tint = (tint_a == 0.0).astype(np.float32)
        ratio = np.where(tint_a > 0.0, saturate(pat_surf[..., 3] / np.maximum(tint_a, np.float32(1e-6))), no_tint)
        s_tint_rgb = mix(tint_rgb, pat_surf[..., :3], (ratio * pat_cov)[..., None])
        s_tint_a = np.maximum(tint_a, pat_surf[..., 3] * pat_cov)
        b_tint_rgb = mix(tint_rgb, pat_sub[..., :3], (np.maximum(no_tint, pat_sub[..., 3]) * pat_cov)[..., None])
        b_tint_a = np.maximum(tint_a, pat_sub[..., 3] * pat_cov)
        surf_mask_t = surf_rgba[..., 3] if respect_tint_mask else np.maximum(surf_rgba[..., 3], pat_cov)
        sub_mask_t = sub_rgba[..., 3] if respect_tint_mask else np.maximum(sub_rgba[..., 3], pat_cov)
        surf_col = mix(surf_rgba[..., :3], _tint_by_luma_gen2(surf_adj, s_tint_rgb, s_tint_a, surf_metal), surf_mask_t[..., None])
        sub_col = mix(sub_rgba[..., :3], _tint_by_luma_gen2(sub_adj, b_tint_rgb, b_tint_a * scalars["sub_trans"], sub_metal), sub_mask_t[..., None])
        bevel_tinted = _tint_by_luma_gen2(dmg_adj, b_tint_rgb, b_tint_a * scalars["sub_trans"], scalars["bevel_metal"])
        bevel_col = mix(surf_col, bevel_tinted, mix(np.float32(1.0), surf_mask_t, bevel_use_tint)[..., None])
        rcb = _vector(p, "g_vPatternRoughnessContrastBrightness", (1.0, 0.0))
        pat_rough = saturate(((surf_rough_xy - np.float32(0.5)) + rcb[1]) * rcb[0] + np.float32(0.5))
        surf_rough_xy = mix(surf_rough_xy, pat_rough, (pat_cov * surf_mask_t)[..., None])
        bevel_burn_col = _apply_weighted_matrix(bevel_col, m_surf_burn, weights)
    else:
        surf_mask_t = surf_rgba[..., 3]
        sub_mask_t = sub_rgba[..., 3]
        surf_col = mix(surf_rgba[..., :3], _tint_by_luma_gen2(surf_adj, tint_rgb, tint_a, surf_metal), surf_mask_t[..., None])
        sub_col = mix(sub_rgba[..., :3], _tint_by_luma_gen2(sub_adj, tint_rgb, tint_a * scalars["sub_trans"], sub_metal), sub_mask_t[..., None])
        bevel_tinted = _tint_by_luma_gen2(dmg_adj, tint_rgb, tint_a * scalars["sub_trans"], scalars["bevel_metal"])
        bevel_col = mix(surf_col, bevel_tinted, mix(np.float32(1.0), surf_mask_t, bevel_use_tint)[..., None])
        bevel_burn_col = _apply_weighted_matrix(bevel_col, m_surf_burn, weights)

        if has_pattern and is_paint_layer:
            paint_cov = (pat_id_mask * pat_surf[..., 3]) * (surf_mask_t if respect_tint_mask else np.float32(1.0))
            surf_col = mix(surf_col, pat_surf[..., :3], paint_cov[..., None])
            surf_metal = mix(surf_metal, pat_props[..., 0], paint_cov)
            surf_rough_xy = mix(surf_rough_xy, pat_props[..., 1:2], paint_cov[..., None])
            if is_puffy:
                thresh = np.float32(_number(p, "g_fPatternTranslucencyThreshold", 0.01))
                puffy_h = saturate(puffy_height - thresh) * paint_cov
                emboss_str = np.float32(_number(p, "g_fPatternEmboss", 0.5))
                puffy_n = _emboss_normal(puffy_height, emboss_str * paint_cov * np.float32(size / 2048.0), size)
                surf_n = _normalize(np.concatenate((surf_n[..., :2] + puffy_n[..., :2], surf_n[..., 2:3]), axis=-1))
                if emboss_str >= 0.0:
                    surf_height = surf_height + puffy_h
                bevel_col = mix(surf_col, pat_surf[..., :3], paint_cov[..., None])
                scalars["dmg_soft"] = mix(scalars["dmg_soft"], np.float32(0.0), puffy_h)
                scalars["bevel_soft"] = mix(scalars["bevel_soft"], np.float32(0.01), puffy_h)
                scalars["bevel_emboss"] = mix(scalars["bevel_emboss"], np.float32(max(0.0, float(emboss_str))), puffy_h)

    # Height-based damage, bevel, burnishing and grime (lines 1901-1948 of ps_older_tintid).
    if "g_tObjectProperties" in textures:
        obj = fit(_with_alpha(textures["g_tObjectProperties"]), size)
    else:
        obj = np.broadcast_to(np.array((1.0, 0.5, 0.0, 0.0), dtype=np.float32), shape + (4,))
    wear_prog = np.float32(linear_to_srgb(wear))
    height_diff = surf_height - sub_height
    dmg_thresh = wear_prog * (vec2s["dmg_mm"][..., 1] - vec2s["dmg_mm"][..., 0]) + vec2s["dmg_mm"][..., 0]
    h = (np.float32(2.0) - dmg_thresh - dmg_val - obj[..., 1] - surf_height) + height_diff
    dmg_soft = np.maximum(scalars["dmg_soft"], np.float32(1e-4))
    surf_mask = smoothstep(-dmg_soft, dmg_soft, (h * height_diff) / dmg_soft)
    eff_height = np.maximum(np.minimum(surf_height, saturate(h)), sub_height)

    bevel_soft = np.maximum(scalars["bevel_soft"], np.float32(1e-4))
    bevel_u = h / bevel_soft
    bevel_mask = smoothstep(np.float32(1.0) + bevel_soft, np.float32(1.0) - bevel_soft, bevel_u)
    bevel_t = saturate(bevel_u)

    burn_raw = saturate((wear_prog - np.float32(1.0)) + obj[..., 2]) * (eff_height + wear_prog)
    sub_burn = smoothstep(vec2s["sub_burn_mm"][..., 0], vec2s["sub_burn_mm"][..., 1], burn_raw)
    surf_burn = smoothstep(vec2s["surf_burn_mm"][..., 0], vec2s["surf_burn_mm"][..., 1], burn_raw)
    burn_sat = saturate(burn_raw)

    crevice_ao = saturate(
        np.float32(1.0)
        - saturate((eff_height + np.float32(0.01)) / dmg_soft)
        * (np.float32(1.0) - saturate((eff_height - np.float32(0.01)) / dmg_soft))
        * surf_mask
        * surf_height
        * np.float32(0.75)
    )
    occlusion = obj[..., 0] * mix(mix(sub_ao, surf_ao, surf_mask) * crevice_ao, np.float32(1.0), sub_burn)

    # Combine base normal, micro normal and damage-bevel emboss.
    flat_n = np.broadcast_to(np.array((0.0, 0.0, 1.0), dtype=np.float32), shape + (3,))
    base_n = _decode_normal(fit(textures["g_tNormal"], size)) if "g_tNormal" in textures else flat_n
    n_scale = mix(np.float32(1.0), scalars["burn_n_scale"], burn_sat)
    comb_xy = base_n[..., :2] + mix(sub_n[..., :2], surf_n[..., :2], surf_mask[..., None]) * n_scale[..., None]
    comb_n = _normalize(np.concatenate((comb_xy, base_n[..., 2:3]), axis=-1))
    emboss_w = smoothstep(-bevel_soft, bevel_soft, bevel_u) * bevel_mask * n_scale * scalars["bevel_emboss"] * np.float32(size / 2048.0) * height_diff
    bevel_n = _emboss_normal(bevel_u, emboss_w, size)
    normal_out = _normalize(mix(comb_n, comb_n + bevel_n - flat_n, np.clip(np.abs(emboss_w), 0.0, 1.0)[..., None]))

    # Roughness, grime, metalness and final albedo.
    rough_xy = mix(sub_rough_xy, mix(surf_rough_xy, bevel_rough_xy, bevel_mask[..., None]), surf_mask[..., None]) * mix(
        np.float32(1.0), mix(scalars["sub_burn_rough"], scalars["surf_burn_rough"], bevel_t), burn_sat
    )[..., None]
    grime_drive = eff_height * occlusion * (wear_prog * np.float32(2.0))
    grime_base = mix(
        smoothstep(vec2s["sub_grime_mm"][..., 0], vec2s["sub_grime_mm"][..., 1], grime_drive),
        smoothstep(vec2s["surf_grime_mm"][..., 0], vec2s["surf_grime_mm"][..., 1], grime_drive),
        mix(surf_mask, np.float32(1.0) - bevel_mask, scalars["bevel_to_sub"]),
    ) * (np.float32(1.0) - np.minimum(rough_xy[..., 0], rough_xy[..., 1]))
    grime_amt = mix(grime_base, np.maximum(grime_base, scalars["burn_grime"]), burn_sat) * grime_rgba[..., 3]
    rough = mix(rough_xy[..., 0], rough_xy[..., 0] * scalars["grime_rough"], grime_amt)
    metal = mix(mix(sub_metal, mix(surf_metal, scalars["bevel_metal"], bevel_mask), surf_mask), scalars["burn_metal"], burn_sat) * (np.float32(1.0) - grime_amt)

    sub_final = mix(sub_col, sub_burn_col, sub_burn[..., None])
    surf_final = mix(surf_col, surf_burn_col, surf_burn[..., None])
    bevel_final = mix(mix(bevel_col, bevel_burn_col, surf_burn[..., None]), sub_final, (scalars["bevel_to_sub"] * (np.float32(1.0) - bevel_t))[..., None])
    combined = mix(sub_final, mix(surf_final, bevel_final, bevel_mask[..., None]), surf_mask[..., None])
    colour = mix(combined, grime_rgba[..., :3], grime_amt[..., None])
    return colour, rough, metal, normal_out, occlusion
