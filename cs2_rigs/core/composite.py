"""Build a finished weapon's textures the way the game does. No Blender imports here.

The game paints a finish onto a weapon by rendering two textures, colour and
roughness/metalness, from the weapon's own textures, the finish's pattern and
colours, and shared wear and grunge textures. `compose` follows that maths for
the nine paint styles, for weapons with the older packed inputs and for the
current ones.

Images are float32 arrays shaped (height, width, channels) with row 0 at the
top, holding the values stored in the file (0..1, not yet linearised).
"""

from __future__ import annotations

import math

import numpy as np

VERSION = 4  # raise when results change, so textures composed earlier are not reused
SOLID, HYDROGRAPHIC, SPRAY, ANODIZED, ANODIZED_MULTI, ANODIZED_AIR, CUSTOM, ANTIQUED, GUNSMITH = range(9)
POSITION_STYLES = (SPRAY, ANODIZED_AIR)  # pattern projected from the weapon's shape, not its layout
PATTERN_STYLES = (HYDROGRAPHIC, SPRAY, ANODIZED_MULTI, ANODIZED_AIR, CUSTOM, ANTIQUED, GUNSMITH)
COLOUR_PATTERN_STYLES = (CUSTOM, ANTIQUED, GUNSMITH)  # pattern is sRGB, not a linear mask
WRAP, MIRROR, CLAMP, BORDER, MIRROR_ONCE = range(5)

DEFAULTS = {
    "g_vColor0": (0.5, 0.5, 0.5),
    "g_vColor1": (0.59, 0.59, 0.59),
    "g_vColor2": (0.38, 0.38, 0.38),
    "g_vColor3": (0.42, 0.42, 0.42),
    "g_flColorBrightness": 1.0,
    "g_flPaintRoughness": 0.6,
    "g_vPaintRoughness": (0.6, 0.6, 0.6, 0.6),
    "g_flPaintMetalness": 0.0,
    "g_vPaintMetalness": (0.0, 0.0, 0.0, 0.0),
    "g_vPaintDurability": (0.0, 0.0, 0.0, 0.0),
    "g_fWearSoftness": 0.0,
    "g_nColorAdjustmentMode": 1,
    "g_bIgnoreWeaponSizeScale": 0,
    "g_flUvScale1": 1.0,
    "g_flWeaponLength1": 36.0,
    "g_vSprayBlend": (1.0, 1.0),
    "g_bBiasSpray": 0,
    "g_bRoughnessUsesPatternUVs": 1,
    "g_bMetalnessUsesPatternUVs": 0,
    "g_nPatternTextureHorizontalSampling": WRAP,
    "g_nPatternTextureVerticalSampling": WRAP,
    "F_ROUGHNESS_TEXTURE": 0,
    "F_ROUGHNESS_PER_COLOR": 0,
    "F_METALNESS_TEXTURE": 0,
    "F_USE_ALL_MASKS": 0,
    "F_SPRAYPAINT_HALFTONE": 0,
    "g_fHalftoneCavityCutoff": 1.0,
    "g_bHalftoneInCavity": 1,
    "g_vHalftonePatternLevels": (0.0, 0.5, 1.0),
    "g_vHalftoneThresholds": (0.0, 1.0),
    "g_flHalftoneRotationOffsetA": 0.0,
    "g_flHalftoneRotationOffsetB": 4.0,
    "g_flHalftoneRotationOffsetC": -4.0,
    "F_OVERLAY_TEXTURE": 0,
    "F_OVERLAY_BLEND_MODE": 0,
    "F_OVERLAY_MASK": 0,
    "g_bOverlayUsesPatternUVs": 0,
    "g_bOverlayUsesUniqueUVs": 0,
    "g_fOverlayStrength": 1.0,
    "g_fOverlayMaterialStrength": 1.0,
    "g_fOverlayBrightness": 1.0,
    "g_fOverlayDurability": 0.0,
    "g_fOverlayRoughness": 0.0,
    "g_fOverlayMetalness": 0.0,
    "g_fOverlayPearlescentMask": 0.0,
    "F_CASE_HARDENING": 0,
    "F_CASE_HARDENING_TRILINEAR": 0,
    "g_flCaseHardeningPatternInfluence": 0.5,
    "g_flCaseHardeningGeometricInfluence": 1.0,
    "g_flCaseHardeningRampOffset": 0.0,
    "g_vCaseHardening": (0.0, 1.0, 0.0, 0.0),
    "F_PATINA_AGE": 0,
    "g_flPatinaAgeStart": 0.0,
    "g_flPatinaColorize": 1.0,
}
for _layer in ("Pattern", "Wear", "Grunge", "Overlay"):
    DEFAULTS[f"g_fl{_layer}TexCoordScale"] = 1.0
    DEFAULTS[f"g_fl{_layer}TexCoordRotation"] = 0.0
    DEFAULTS[f"g_v{_layer}TexCoordOffset"] = (0.0, 0.0)
STYLE_DEFAULTS = {ANODIZED_MULTI: {"g_flPaintRoughness": 0.2}, ANODIZED_AIR: {"g_flPaintRoughness": 0.2}}

PAINT_LEVELS = (0.045, -1.4427 * math.log(1 - 0.6), 1.0)
METALLIC_PAINT_LEVELS = (0.08, -1.4427 * math.log(1 - 0.6), 1.0)
ALBEDO_LEVELS = np.array((0.045, -1.4427 * math.log(1 - 0.4), 0.85), dtype=np.float32)
DARK_METALLIC_LEVELS = np.array((0.1, -1.4427 * math.log(1 - 0.4), 0.92), dtype=np.float32)
METALLIC_LEVELS = np.array((0.45, -1.4427 * math.log(1 - 0.4), 0.92), dtype=np.float32)
COLOR_BOOST_FACTOR = 63.0
LUMA = np.array((0.2125, 0.7154, 0.0721), dtype=np.float32)
BARE_ANODIZED = np.array((0.38, 0.37, 0.35), dtype=np.float32)
# 17-tap disk offsets used by the shader to smooth g_tPosition before triplanar lookup.
_POSITION_TAPS = (
    (-0.00107234, -0.00400203), (0.00195312, -0.00338291), (0.00400203, -0.00107234),
    (-0.000714896, -0.00266802), (0.000976565, -0.00169146), (0.00266802, -0.000714896),
    (-0.00338291, -0.00195312), (-0.00169146, -0.000976565), (0.0, 0.0),
    (0.00169146, 0.000976565), (0.00338291, 0.00195312), (-0.00266802, 0.000714896),
    (-0.000976565, 0.00169146), (0.000714896, 0.00266802), (-0.00400203, 0.00107234),
    (-0.00195312, 0.00338291), (0.00107234, 0.00400202),
)
_CH_BOX_OFFSETS = ((1.0 / 2048.0, 1.0 / 2048.0), (-1.0 / 2048.0, -1.0 / 2048.0), (-1.0 / 2048.0, 1.0 / 2048.0), (1.0 / 2048.0, -1.0 / 2048.0))


def srgb_to_linear(x):
    x = np.asarray(x, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(x):
    x = np.clip(np.asarray(x, dtype=np.float32), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055).astype(np.float32)


def saturate(x):
    return np.clip(x, 0.0, 1.0)


def mix(a, b, t):
    return a + (b - a) * t


def smoothstep(low, high, x):
    """As in shaders, including `low` above `high` for a falling edge."""
    span = np.asarray(high - low, dtype=np.float32)
    span = np.where(span == 0, np.float32(1e-6), span)
    t = saturate((x - low) / span)
    return t * t * (3.0 - 2.0 * t)


def luma(rgb):
    return rgb @ LUMA


def _by_masks(values, masks):
    """Pick between four values with three masks: the first where no mask is set."""
    a, b, c, d = (np.asarray(v, dtype=np.float32) for v in values)
    if a.ndim:  # colours: let the masks spread over the channels
        masks = masks[..., None, :]
    return mix(mix(mix(a, b, masks[..., 0]), c, masks[..., 1]), d, masks[..., 2])


def _wrap(coord, size, mode):
    if mode == CLAMP or mode == BORDER:
        return np.clip(coord, 0, size - 1)
    if mode == MIRROR:
        coord = np.mod(coord, 2 * size)
        return np.where(coord >= size, 2 * size - 1 - coord, coord)
    if mode == MIRROR_ONCE:
        return np.clip(np.where(coord < 0, -1 - coord, coord), 0, size - 1)
    return np.mod(coord, size)


def sample(image, u, v, mode_u=CLAMP, mode_v=CLAMP):
    """Bilinear lookup of `image` at texture coordinates, v running down."""
    height, width = image.shape[:2]
    x = u * width - 0.5
    y = v * height - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0)[..., None].astype(np.float32)
    fy = (y - y0)[..., None].astype(np.float32)
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)
    xa, xb = _wrap(x0, width, mode_u), _wrap(x0 + 1, width, mode_u)
    ya, yb = _wrap(y0, height, mode_v), _wrap(y0 + 1, height, mode_v)
    top = image[ya, xa] * (1 - fx) + image[ya, xb] * fx
    bottom = image[yb, xa] * (1 - fx) + image[yb, xb] * fx
    return top * (1 - fy) + bottom * fy


def fit(image, size):
    """`image` resampled onto a size x size grid."""
    while image.shape[0] >= 2 * size and image.shape[1] >= 2 * size and image.shape[0] % 2 == 0 and image.shape[1] % 2 == 0:
        image = (image[0::2, 0::2] + image[1::2, 0::2] + image[0::2, 1::2] + image[1::2, 1::2]) * 0.25
    if image.shape[0] == size and image.shape[1] == size:
        return image
    centres = (np.arange(size, dtype=np.float32) + 0.5) / size
    u, v = np.meshgrid(centres, centres)
    return sample(image, u, v)


def _round2(value):
    return math.floor((value + 0.005) * 100) / 100


def uv_transform(rotation, scale, offset):
    """The two rows that place a layer: u' = row0 . (u, v, 1), v' = row1 . (u, v, 1)."""
    scale = _round2(scale)
    angle = math.radians(_round2(rotation))
    cos, sin = math.cos(angle), math.sin(angle)
    half = 0.5 / (scale if scale != 0 else 1.0)
    pivot_u = half * cos + half * sin
    pivot_v = -pivot_u * sin + half * cos
    row0 = (cos * scale, -sin * scale, scale * cos * pivot_u - scale * sin * pivot_v + _round2(offset[0]) - 0.5)
    row1 = (sin * scale, cos * scale, scale * sin * pivot_u + scale * cos * pivot_v + _round2(offset[1]) - 0.5)
    return row0, row1


def _place(rows, u, v):
    (a, b, c), (d, e, f) = rows
    return a * u + b * v + c, d * u + e * v + f


def _levels(source, paint, metalness, wear):
    """Pull worn paint toward the brightness range paint can have in the game."""
    levels = mix(np.asarray(PAINT_LEVELS, dtype=np.float32), np.asarray(METALLIC_PAINT_LEVELS, dtype=np.float32), np.asarray(metalness, dtype=np.float32)[..., None])
    floor, power, ceiling = levels[..., 0], levels[..., 1], levels[..., 2]
    hue = np.maximum(paint, 0.0003)
    hue = hue / np.linalg.norm(hue, axis=-1, keepdims=True)
    value = mix(np.minimum(floor, luma(source)), ceiling, saturate(paint.max(axis=-1) ** power))
    fitted = hue * (value / hue.max(axis=-1))[..., None]
    return mix(paint, fitted, wear)


def _albedo_remap(hue_src, val_src, levels, floor=None, scale=1.0, boost=0.0):
    """Map blended RGB values back into the game's calibrated albedo range."""
    hue = np.maximum(hue_src, 0.0003)
    norm = hue / np.linalg.norm(hue, axis=-1, keepdims=True) * 1.06
    lum = luma(saturate(val_src * scale))
    lvl = np.asarray(levels, dtype=np.float32)
    floor_v = lvl[..., 0] if floor is None else np.asarray(floor, dtype=np.float32)
    min_col = (norm * floor_v[..., None] * 1.732 / np.maximum(np.linalg.norm(norm, axis=-1, keepdims=True), 1e-6)) / np.maximum(luma(norm), 1e-6)[..., None]
    val_t = saturate(np.maximum(val_src.max(axis=-1) * scale, 0.0) ** lvl[..., 1])
    mapped = np.maximum(min_col, norm * mix(lvl[..., 0], lvl[..., 2], val_t)[..., None])
    if boost:
        mapped = mix(mapped, np.minimum(lvl[..., 2:3], mapped + (lum * 2.0)[..., None]), boost)
    return mix(floor_v[..., None], mapped, np.sqrt(smoothstep(0.0003, floor_v, lum))[..., None])


def _overlay_blend(dst, src, weight, mode):
    """Combine an overlay colour onto `dst` using one of the shader's five blend modes."""
    w = weight[..., None]
    if mode in (0, 4):
        return mix(dst, src, w)
    if mode == 1:
        return mix(dst, _albedo_remap(src * dst, dst, ALBEDO_LEVELS), w)
    if mode == 2:
        prod = dst * src
        return mix(dst, _albedo_remap(prod, prod, ALBEDO_LEVELS), w)
    if mode == 3:
        added = dst + src
        return mix(dst, _albedo_remap(added, added, ALBEDO_LEVELS, scale=2.0, boost=1.0 / COLOR_BOOST_FACTOR), w)
    return dst


def _overlay_alpha(alpha, style, mode, masks, pattern, use_all_masks, overlay_mask):
    """Mask an overlay's alpha according to F_OVERLAY_MASK and the active paint style."""
    if style in (CUSTOM, ANTIQUED, GUNSMITH):
        return alpha * overlay_mask if mode == 8 else alpha
    if style == ANODIZED or mode in (0, 6):
        return alpha
    if style == SOLID:
        m0, m1, m2 = masks[..., 0], masks[..., 1], masks[..., 2]
        if mode == 1:
            return alpha * (1.0 - np.maximum(m0, np.maximum(m1, m2)))
        if mode == 2:
            return alpha * (m0 * (1.0 - np.maximum(m1, m2)))
        if mode == 3:
            return alpha * (m1 * (1.0 - m2))
        if mode == 4:
            return alpha * m2
        if mode == 5:
            return alpha * np.maximum(m0, np.maximum(m1, m2))
        return alpha
    p0, p1, p2 = pattern[..., 0], pattern[..., 1], pattern[..., 2]
    if style == SPRAY and not use_all_masks:
        if mode == 1:
            return alpha * (1.0 - np.maximum(p0, np.maximum(p1, p2)))
        if mode == 2:
            return alpha * (p0 * (1.0 - np.maximum(p1, p2)))
        if mode == 3:
            return alpha * (p1 * (1.0 - p2))
        if mode == 4:
            return alpha * p2
        if mode == 5:
            return alpha * np.maximum(p0, np.maximum(p1, p2))
        return alpha
    m1, m2 = masks[..., 1], masks[..., 2]
    if mode == 1:
        return alpha * (1.0 - np.maximum(p0, np.maximum(p1, p2))) * (1.0 - np.maximum(m1, m2))
    if mode == 2:
        return alpha * (p0 * (1.0 - np.maximum(p1, p2))) * (1.0 - np.maximum(m1, m2))
    if mode == 3:
        return alpha * (np.maximum(p1 * (1.0 - p2), m1) * (1.0 - m2))
    if mode == 4:
        return alpha * np.maximum(p2 * (1.0 - m1), m2)
    if mode == 5:
        return alpha * np.maximum(p0, np.maximum(p1, np.maximum(p2, np.maximum(m1, m2))))
    if mode == 7:
        return alpha * (1.0 - np.maximum(m1, m2))
    return alpha


def _halftone_pattern(pattern, pat_image, pos, facing, cavity, shade, p, fitted, pat_modes, triplanar):
    """Apply the three-screen spraypaint halftone pass to a triplanar pattern."""
    base_rot = float(p["g_flPatternTexCoordRotation"])
    scale = float(p["g_flPatternTexCoordScale"]) * fitted
    offset = p["g_vPatternTexCoordOffset"]
    alpha_img = pat_image[..., 3:4]
    dots = np.stack(
        [
            triplanar(alpha_img, uv_transform(base_rot + float(p[f"g_flHalftoneRotationOffset{ch}"]), scale, offset), pat_modes, pos, facing)[..., 0]
            for ch in ("A", "B", "C")
        ],
        axis=-1,
    )
    lx, ly, lz = (float(x) for x in p["g_vHalftonePatternLevels"][:3])
    ht_low, ht_pow, ht_high = -lx, -1.4427 * math.log(max(0.0001, 1.0 - ly)), 2.0 - lz
    tx, ty = float(p["g_vHalftoneThresholds"][0]), float(p["g_vHalftoneThresholds"][1])
    if p["g_bHalftoneInCavity"]:
        grad = smoothstep(ht_low, ht_high, np.maximum(np.minimum(cavity, float(p["g_fHalftoneCavityCutoff"])) * shade * 2.0, 0.0) ** ht_pow)
        factor = grad if tx > ty else (1.0 - grad)
    else:
        grad = smoothstep(ht_low, ht_high, np.maximum(cavity * shade * 2.0, 0.0) ** ht_pow)
        factor = (1.0 - grad) if tx > ty else grad
    rgb = smoothstep(tx, ty, pattern[..., :3] * factor[..., None] * dots)
    return np.concatenate((rgb, pattern[..., 3:4]), axis=-1)


def _hemi_oct(normal):
    """Re-encode a decoded tangent-space normal map to Source 2's HemiOct RG channels."""
    if normal.shape[-1] < 3:
        return normal[..., :2]
    n = normal[..., :3] * 2.0 - 1.0
    nz = np.maximum(n[..., 2], 0.0)
    denom = np.maximum(np.abs(n[..., 0]) + np.abs(n[..., 1]) + nz, 1e-6)
    return np.stack(((n[..., 0] + n[..., 1]) / denom * 0.5 + 0.5, (n[..., 0] - n[..., 1]) / denom * 0.5 + 0.5), axis=-1).astype(np.float32)


def _smoothed_position(textures, u, v, size):
    """17-tap disk blur of g_tPosition (* 2), matching the triplanar shader lookup."""
    image = textures.get("g_tPosition")
    if image is None:
        return np.broadcast_to(np.asarray((1.0, 1.0, 1.0), dtype=np.float32), (size, size, 3))
    acc = np.zeros((size, size, 3), dtype=np.float32)
    pos_rgb = image[..., :3]
    for dx, dy in _POSITION_TAPS:
        acc += sample(pos_rgb, u + dx * 0.2, v + dy * 0.2, CLAMP, CLAMP)
    return acc * (2.0 / len(_POSITION_TAPS))


def _patina_age_pass(style, p, textures, u, v, size, wear, cavity, patina_area, wear_tex, grunge_raw, pattern, pattern_alpha, base, base_surface, colours, unpainted, scuff, exposure, edge, softness_value, worn_through, paint_roughness, paint_metalness):
    """Ageing patina pass (F_PATINA_AGE) for Antiqued and Gunsmith finishes."""
    inv_cav = 1.0 - cavity
    cav_sq = inv_cav ** 4 * 0.25
    age_mask = sample(textures["g_tPatinaAgeMask"], u, v, WRAP, WRAP)[..., 0] if "g_tPatinaAgeMask" in textures else np.zeros((size, size), dtype=np.float32)
    pos_tex = textures.get("g_tPosition")
    pos_z = np.full((size, size), 0.5, dtype=np.float32) if pos_tex is None else fit(pos_tex, size)[..., 2]
    nrm_tex = textures.get("g_tNormal")
    nrm = np.full((size, size, 2), 0.5, dtype=np.float32) if nrm_tex is None else _hemi_oct(sample(nrm_tex, u, v, WRAP, WRAP))
    nrm_edge = smoothstep(0.05, 0.5, np.minimum(nrm, 1.0 - nrm))

    grunge_pat_rgb = mix(
        mix(1.0, saturate(np.maximum(luma(grunge_raw[..., :3]), 0.0) ** 0.25 * 2.0), cav_sq * 2.0 + 0.75 * wear)[..., None],
        grunge_raw[..., :3],
        patina_area[..., None],
    )
    age = mix(float(p["g_flPatinaAgeStart"]), 0.9, math.sqrt(wear))
    verdigris = np.minimum(
        1.0 - smoothstep(0.0, 0.1 + wear_tex * 0.33, cavity) ** 4,
        mix(smoothstep(0.2, 0.42, pos_z) * wear, 0.0, age_mask * inv_cav),
    )
    ramp_u = np.clip(
        (smoothstep(0.5, 1.0, inv_cav) ** 2 * (wear_tex + (1.0 - nrm_edge[..., 0] * nrm_edge[..., 1])) + age * 0.75)
        * mix(0.001, 1.0, np.maximum(mix(age, 0.0, age_mask), 0.0) ** 0.9),
        0.004,
        0.996,
    )
    ramp_tex = textures.get("g_tPatinaAgeColorRamp")
    if ramp_tex is None:
        ramp_tex = np.ones((2, 2, 4), dtype=np.float32)
    elif ramp_tex.shape[-1] == 3:
        ramp_tex = np.concatenate((ramp_tex, np.ones_like(ramp_tex[..., :1])), axis=-1)
    ramp_tex = np.concatenate((srgb_to_linear(ramp_tex[..., :3]), ramp_tex[..., 3:4]), axis=-1)
    ramp = sample(ramp_tex, ramp_u, np.full_like(ramp_u, 0.5), CLAMP, CLAMP)
    ox = ramp[..., 3]

    grunge_lum = luma(saturate(grunge_pat_rgb))
    g_levels = mix(ALBEDO_LEVELS, mix(METALLIC_LEVELS, DARK_METALLIC_LEVELS, smoothstep(0.55, 0.0156, grunge_lum)[..., None]), ox[..., None])
    g_floor = mix(ALBEDO_LEVELS[0], DARK_METALLIC_LEVELS[0], ox)
    grunge_tinted = mix(grunge_pat_rgb, _albedo_remap(mix(grunge_pat_rgb, ramp[..., :3], 0.8), grunge_pat_rgb, g_levels, floor=g_floor), patina_area[..., None])

    pat_rgb = pattern[..., :3]
    pat_lum = luma(saturate(pat_rgb))
    p_levels = mix(METALLIC_LEVELS, DARK_METALLIC_LEVELS, smoothstep(0.55, 0.0156, pat_lum)[..., None])
    pat_mapped = _albedo_remap(mix(ramp[..., :3] * pat_rgb, ramp[..., :3], float(p["g_flPatinaColorize"])), pat_rgb, p_levels, floor=DARK_METALLIC_LEVELS[0])
    pat_tint = mix(pat_mapped, ramp[..., :3], ox[..., None])

    rough_base = paint_roughness()
    metalness = paint_metalness()
    patina_metal = 1.0 - ox
    grunge_dull = (1.0 - grunge_raw[..., 3]) * wear * wear * 0.5

    if style == GUNSMITH:
        hardness = np.maximum(0.0, smoothstep(0.0, 0.5, pattern_alpha))
        amount = ((np.minimum(unpainted, 1.0 - patina_area) + scuff) * (wear * 6.0 + 1.0) + edge) * hardness
        all_patina = patina_area > 0.99
        bare = np.where(all_patina, amount, worn_through(amount, softness_value * hardness))
        thin = 1.0 - np.minimum(1.0, pattern_alpha * 2.0)
        rough_base = mix(rough_base, np.where(pattern_alpha >= 0.5, rough_base, thin * thin * 0.85 + 0.15), patina_area)
        pat_rough = mix(rough_base, 0.5 * ((2.0 - luma(grunge_tinted)) - wear_tex), np.maximum(verdigris, ox))
        rough = mix(np.minimum(1.0, pat_rough + grunge_dull), pat_rough, patina_area)
        pat_tint = mix(pat_rgb, pat_tint, patina_area[..., None])
        paint = mix(pat_tint * grunge_tinted, mix(pat_tint, grunge_tinted, verdigris[..., None]), patina_area[..., None])
        source = mix(pat_rgb, pat_rgb * colours[1], patina_area[..., None])
        fitted = _levels(source, paint, mix(metalness, np.float32(1.0), patina_area), wear)
        out_colour = mix(fitted, base, (bare * (1.0 - patina_area))[..., None])
        out_rough = mix(base_surface[..., 0], rough, np.where(all_patina, 1.0, np.maximum(0.0, 1.0 - bare)))
        out_metal = mix(mix(metalness, base_surface[..., 1], bare), patina_metal, patina_area)
        left = 1.0 - bare
    else:
        pat_rough = mix(rough_base, 0.5 * ((2.0 - luma(grunge_tinted)) - wear_tex), np.maximum(verdigris, ox))
        rough = mix(np.minimum(1.0, pat_rough + grunge_dull), pat_rough, patina_area)
        paint = mix(pat_tint * grunge_tinted, mix(pat_tint, grunge_tinted, verdigris[..., None]), patina_area[..., None])
        fitted = _levels(pat_rgb * colours[1], paint, np.float32(1.0), wear)
        out_colour = mix(fitted, base, (1.0 - patina_area)[..., None])
        out_rough = mix(base_surface[..., 0], rough, (unpainted <= 0.996) * patina_area)
        out_metal = mix(base_surface[..., 1], patina_metal, patina_area)
        left = 1.0 - unpainted
    return out_colour, out_rough, out_metal, left


def compose(style, params, textures, size, wear=0.0, separate_inputs=False):
    """Colour and surface textures for one finish on one weapon.

    `params` holds the finish's shader values by their game names, with the
    weapon's own `g_flUvScale1` and `g_flWeaponLength1` merged in. `textures`
    maps game texture names to images: the weapon's `g_tColor`, `g_tMetalness`,
    `g_tAmbientOcclusion`, `g_tMasks`, `g_tPosition` and `g_tSurface`, the
    shared `g_tWear` and `g_tGrunge`, and any finish-specific maps (`g_tPattern`,
    `g_tPaintRoughness`, `g_tPaintMetalness`, `g_tCaseHardeningColorRamp`,
    `g_tOverlay`, `g_tOverlayMask`, `g_tPatinaAgeColorRamp`, `g_tPatinaAgeMask`,
    `g_tNormal`). `separate_inputs` is set for weapons whose inputs come as
    separate channels (the current models).

    Returns `colour` (sRGB encoded), `roughness`, `metalness` and `paint` (1
    where paint is left) as arrays of the requested size.
    """
    p = {**DEFAULTS, **STYLE_DEFAULTS.get(style, {}), **params}
    wear = float(min(max(wear, 0.0), 1.0))
    centres = (np.arange(size, dtype=np.float32) + 0.5) / size
    u, v = np.meshgrid(centres, centres)

    def weapon(name, channels, fallback):
        image = textures.get(name)
        if image is None:
            return np.broadcast_to(np.asarray(fallback, dtype=np.float32), (size, size, channels))
        return fit(image, size)[..., :channels]

    def colour(name):
        return srgb_to_linear(p[name])[:3]

    def layer_scale(by_length=False):
        if p["g_bIgnoreWeaponSizeScale"]:
            return 1.0
        return p["g_flWeaponLength1"] / 36.0 if by_length else p["g_flUvScale1"]

    def layer_rows(layer, by_length=False):
        return uv_transform(p[f"g_fl{layer}TexCoordRotation"], p[f"g_fl{layer}TexCoordScale"] * layer_scale(by_length), p[f"g_v{layer}TexCoordOffset"])

    by_length = style in POSITION_STYLES
    occlusion = weapon("g_tAmbientOcclusion", 4, (1.0, 1.0, 1.0, 0.0))
    cavity = srgb_to_linear(occlusion[..., 0])
    shade = srgb_to_linear(occlusion[..., 1])
    unpainted = occlusion[..., 3]
    cavity_raw = cavity
    if separate_inputs:
        cavity_raw = cavity ** 1.5
        cavity = cavity_raw * 0.96
    masks = weapon("g_tMasks", 3, (1.0, 0.0, 0.0))
    base = srgb_to_linear(weapon("g_tColor", 3, (0.5, 0.5, 0.5)))
    base_surface = weapon("g_tMetalness", 2, (0.5, 0.0))
    wear_tex = sample(textures["g_tWear"], *_place(layer_rows("Wear", by_length), u, v), WRAP, WRAP)[..., 0]
    grunge_raw = sample(textures["g_tGrunge"], *_place(layer_rows("Grunge", by_length), u, v), WRAP, WRAP)
    grunge_raw = np.concatenate((srgb_to_linear(grunge_raw[..., :3]), grunge_raw[..., 3:4]), axis=-1)
    grunge = mix(1.0, grunge_raw, ((1.0 - cavity) ** 4 * 0.25 + 0.75 * wear)[..., None])

    if not separate_inputs:
        scuff = wear_tex * cavity
    elif style == SPRAY:
        scuff = wear_tex * mix(smoothstep(0.0, 0.68, cavity ** 1.1), smoothstep(0.0, 0.51, cavity ** 0.8), wear ** 0.3)
    else:
        scuff = wear_tex * mix(smoothstep(0.0, 0.72, cavity ** 1.3), smoothstep(0.0, 0.4, cavity), wear ** 1.2)
    ov_low, ov_high = (0.56, 0.74) if separate_inputs else (0.58, 0.68)
    low, high = (0.56, 0.74) if separate_inputs and style != GUNSMITH else (0.58, 0.68)

    def worn_through(amount, softness):
        return smoothstep(low - softness, high + softness, amount)

    def fresh_edge(amount, bare, softness):
        """0 where the paint is pristine, which is where brightening applies."""
        if separate_inputs:
            return smoothstep(0.53 - softness, 0.72 + softness, amount)
        return smoothstep(0.0, 0.01, bare)

    use_all_masks = style == SPRAY and bool(p["F_USE_ALL_MASKS"])
    halftone = bool(p["F_SPRAYPAINT_HALFTONE"]) and style in POSITION_STYLES
    case_hardening = bool(p["F_CASE_HARDENING"]) and style in (SOLID, ANTIQUED, GUNSMITH)
    ch_trilinear = case_hardening and bool(p["F_CASE_HARDENING_TRILINEAR"])
    patina_age = bool(p["F_PATINA_AGE"]) and not case_hardening and style in (ANTIQUED, GUNSMITH)
    overlay = bool(p["F_OVERLAY_TEXTURE"])

    def triplanar(image, rows, modes, position, facing):
        x, y, z = position[..., 0], position[..., 1], position[..., 2]
        side = sample(image, *_place(rows, y, z), *modes)
        top = sample(image, *_place(rows, x, z), *modes)
        front = sample(image, *_place(rows, y, x), *modes)
        blend_top, blend_front = p["g_vSprayBlend"][0], p["g_vSprayBlend"][1]
        return mix(mix(side, top, (blend_top * np.abs(facing[..., 1]) ** 7)[..., None]), front, (blend_front * np.abs(facing[..., 2]) ** 7)[..., None])

    def surface_facing():
        facing = weapon("g_tSurface", 3, (0.5, 0.5, 1.0))
        if separate_inputs and p["g_bBiasSpray"]:
            facing = linear_to_srgb(facing)
        facing = facing * 2.0 - 1.0
        return facing / np.maximum(np.linalg.norm(facing, axis=-1, keepdims=True), 1e-6)

    pattern = None
    pat_image = pat_rows = pat_u = pat_v = ch_pos = ch_facing = None
    pat_modes = (int(p["g_nPatternTextureHorizontalSampling"]), int(p["g_nPatternTextureVerticalSampling"]))
    if style in PATTERN_STYLES or case_hardening:
        pat_rows = layer_rows("Pattern", by_length)
        pat_image = textures["g_tPattern"]
        if pat_image.shape[-1] == 3:
            pat_image = np.concatenate((pat_image, np.ones_like(pat_image[..., :1])), axis=-1)
        if style in COLOUR_PATTERN_STYLES:
            pat_image = np.concatenate((srgb_to_linear(pat_image[..., :3]), pat_image[..., 3:4]), axis=-1)
        if style in POSITION_STYLES:
            position = _smoothed_position(textures, u, v, size) if halftone else weapon("g_tPosition", 3, (0.5, 0.5, 0.5)) * 2.0
            if halftone:
                position = position.copy()
                position[..., 2] *= -1.0
            facing = surface_facing()
            pattern = triplanar(pat_image, pat_rows, pat_modes, position, facing)
            if halftone:
                pattern = _halftone_pattern(pattern, pat_image, position, facing, cavity, shade, p, layer_scale(by_length), pat_modes, triplanar)
        elif ch_trilinear:
            ch_pos = _smoothed_position(textures, u, v, size)
            ch_facing = surface_facing()
            pattern = triplanar(pat_image, pat_rows, pat_modes, ch_pos, ch_facing)
        else:
            pat_u, pat_v = _place(pat_rows, u, v)
            pattern = sample(pat_image, pat_u, pat_v, *pat_modes)

    ch_ramp = None
    if case_hardening:
        ramp_tex = textures.get("g_tCaseHardeningColorRamp")
        if ramp_tex is None:
            ramp_tex = np.ones((2, 2, 4), dtype=np.float32)
        elif ramp_tex.shape[-1] == 3:
            ramp_tex = np.concatenate((ramp_tex, np.ones_like(ramp_tex[..., :1])), axis=-1)
        ramp_tex = np.concatenate((srgb_to_linear(ramp_tex[..., :3]), ramp_tex[..., 3:4]), axis=-1)
        nrm_tex = textures.get("g_tNormal")
        nrm = np.full((size, size, 2), 0.5, dtype=np.float32) if nrm_tex is None else _hemi_oct(sample(nrm_tex, u, v, WRAP, WRAP))
        edge_nrm = smoothstep(0.85, 0.20, np.minimum(nrm, 1.0 - nrm))
        geo_inf = float(p["g_flCaseHardeningGeometricInfluence"])
        pat_inf = float(p["g_flCaseHardeningPatternInfluence"])
        ramp_off = float(p["g_flCaseHardeningRampOffset"])
        geo = mix(0.5, np.maximum(cavity, 0.0) ** 0.85, geo_inf) * mix(1.0, shade * (np.minimum(edge_nrm[..., 0], edge_nrm[..., 1]) * 2.0), geo_inf)
        ramp_v = np.maximum(pattern[..., 1] * pat_inf, (1.0 - shade) * 0.2 * geo_inf) + ramp_off

        def ramp_lookup(pat_x):
            return sample(ramp_tex, mix(geo * 2.0, geo + pat_x, pat_inf), ramp_v, CLAMP, CLAMP)

        ch_ramp = ramp_lookup(pattern[..., 0])
        if style in (SOLID, GUNSMITH):
            for dx, dy in _CH_BOX_OFFSETS:
                if ch_trilinear:
                    tap_x = triplanar(pat_image, pat_rows, pat_modes, ch_pos + np.array((0.0, dx, dy), dtype=np.float32), ch_facing)[..., 0]
                else:
                    tap_x = sample(pat_image, pat_u + dx, pat_v + dy, *pat_modes)[..., 0]
                ch_ramp = ch_ramp + ramp_lookup(tap_x)
            ch_ramp = ch_ramp * 0.2
        if style == ANTIQUED:
            pattern = ch_ramp
        elif style == GUNSMITH:
            pattern = mix(pattern, ch_ramp, masks[..., 1:2])

    ov_rgb = ov_raw_w = ov_strength_a = None
    ov_mask_mode = int(p["F_OVERLAY_MASK"])
    ov_blend_mode = int(p["F_OVERLAY_BLEND_MODE"])
    ov_dur = 1.0 - float(p["g_fOverlayDurability"])
    ov_mat_strength = float(p["g_fOverlayMaterialStrength"])
    ov_rough = float(p["g_fOverlayRoughness"])
    ov_metal = float(p["g_fOverlayMetalness"])
    if overlay:
        ov_tex = textures.get("g_tOverlay")
        if ov_tex is None:
            ov_tex = np.ones((2, 2, 4), dtype=np.float32)
        elif ov_tex.shape[-1] == 3:
            ov_tex = np.concatenate((ov_tex, np.ones_like(ov_tex[..., :1])), axis=-1)
        ov_tex = np.concatenate((srgb_to_linear(ov_tex[..., :3]), ov_tex[..., 3:4]), axis=-1)
        if p["g_bOverlayUsesUniqueUVs"]:
            ov_u, ov_v = _place(layer_rows("Overlay", by_length), u, v)
        elif p["g_bOverlayUsesPatternUVs"]:
            ov_u, ov_v = _place(layer_rows("Pattern", by_length), u, v)
        else:
            ov_u, ov_v = u, v
        ov_sample = sample(ov_tex, ov_u, ov_v, WRAP, WRAP)
        ov_mask_tex = sample(textures["g_tOverlayMask"], u, v, WRAP, WRAP)[..., 0] if (ov_mask_mode == 8 and "g_tOverlayMask" in textures) else np.ones((size, size), dtype=np.float32)
        ov_raw_w = _overlay_alpha(ov_sample[..., 3], style, ov_mask_mode, masks, pattern, use_all_masks, ov_mask_tex)
        ov_strength_a = ov_raw_w * float(p["g_fOverlayStrength"])
        ov_rgb = ov_sample[..., :3] * float(p["g_fOverlayBrightness"])

    def overlay_wear(bare_val, softness_val):
        ov_bare = smoothstep(ov_low - softness_val, ov_high + softness_val, scuff * (wear * 6.0 + 1.0) * ov_dur)
        if ov_mask_mode == 0:
            bare_val = np.maximum(0.0, mix(bare_val, np.minimum(bare_val, ov_bare), ov_strength_a))
        ov_w = ov_strength_a * saturate(1.0 - ov_bare) if ov_blend_mode == 4 else ov_strength_a
        ov_mat_w = (1.0 - ov_bare) * ov_w * ov_mat_strength
        return bare_val, ov_bare, ov_w, ov_mat_w

    pattern_alpha = pattern[..., 3] if pattern is not None else np.float32(1.0)
    # Pattern alpha between a half and one marks edges that are already bare.
    edge = smoothstep(0.5, 0.6, pattern_alpha) * smoothstep(1.0, 0.9, pattern_alpha)

    durability = 1.0 - np.asarray(p["g_vPaintDurability"], dtype=np.float32)
    softness_value = float(p["g_fWearSoftness"])
    brightness = float(p["g_flColorBrightness"])
    colours = [colour(f"g_vColor{i}") for i in range(4)]
    exposure = (unpainted + scuff) * (wear * 6.0 + 1.0)
    grunge_dulling = (1.0 - grunge[..., 3]) * wear * wear * 0.5

    def painted(switch, texture, on_pattern, value):
        """A paint property that is one number, or a texture when the finish brings one."""
        if p[switch] and texture in textures:
            where = _place(layer_rows("Pattern", by_length), u, v) if p[on_pattern] else (u, v)
            return sample(textures[texture], *where, WRAP, WRAP)[..., 0]
        return np.float32(p[value])

    def paint_roughness():
        return painted("F_ROUGHNESS_TEXTURE", "g_tPaintRoughness", "g_bRoughnessUsesPatternUVs", "g_flPaintRoughness")

    def paint_metalness():
        return painted("F_METALNESS_TEXTURE", "g_tPaintMetalness", "g_bMetalnessUsesPatternUVs", "g_flPaintMetalness")

    if style in (SOLID, HYDROGRAPHIC, SPRAY):
        ov_dur_scale = mix(1.0, ov_dur, ov_raw_w) if overlay else np.float32(1.0)
        if style == SOLID:
            zones = masks
            hardness = _by_masks(durability, zones)
            amount = exposure * ov_dur_scale * hardness
        elif style == HYDROGRAPHIC:
            hardness = mix(mix(_by_masks(durability, pattern), durability[2], masks[..., 1]), durability[3], masks[..., 2])
            extra_m = saturate(masks[..., 1] + masks[..., 2])
            pat_h = smoothstep(0.0, 0.5, pattern_alpha)
            if overlay:
                h_factor = (np.maximum(extra_m, pat_h) if ov_mask_mode == 0 else pat_h) * ov_dur_scale
                if ov_mask_mode > 0:
                    h_factor = np.maximum(extra_m, h_factor)
            else:
                h_factor = np.maximum(extra_m, pat_h)
            amount = (exposure + edge) * h_factor * hardness
        else:
            hardness = _by_masks(durability, pattern)
            if use_all_masks:
                hardness = mix(mix(hardness, durability[2], masks[..., 1]), durability[3], masks[..., 2])
            amount = exposure * ov_dur_scale * hardness
        softness = softness_value * hardness
        if style != SOLID:
            # Each colour of the pattern wears away a little before the next.
            first = 0.56 - 0.06 * wear
            second = 0.54 - 0.12 * wear
            third = 0.52 - 0.18 * wear
            zones = pattern[..., :3] * np.stack(
                (smoothstep(0.58 + softness, first - softness, amount), smoothstep(first + softness, second - softness, amount), smoothstep(second + softness, third - softness, amount)),
                axis=-1,
            )
        bare = worn_through(amount, softness)
        if overlay:
            bare, _, ov_w, ov_mat_w = overlay_wear(bare, softness)

        def per_zone(values):
            picked = _by_masks(values, zones)
            if style == HYDROGRAPHIC or use_all_masks:
                third_value, fourth_value = (np.asarray(x, dtype=np.float32) for x in values[2:4])
                extra = masks[..., None, 1:3] if third_value.ndim else masks[..., 1:3]
                picked = mix(mix(picked, third_value, extra[..., 0]), fourth_value, extra[..., 1])
            return picked

        metalness = per_zone(p["g_vPaintMetalness"])
        if p["F_ROUGHNESS_PER_COLOR"]:
            rough = per_zone(p["g_vPaintRoughness"])
        elif style == SOLID and p["F_ROUGHNESS_TEXTURE"] and "g_tPaintRoughness" in textures:
            rough = sample(textures["g_tPaintRoughness"], u, v, WRAP, WRAP)[..., 0]
        else:
            rough = np.float32(p["g_flPaintRoughness"])
        if style == SOLID and case_hardening:
            ch_mix = (tuple(p["g_vCaseHardening"]) + (0.0, 0.0, 0.0, 0.0))[:4]
            tint = _by_masks([mix(colours[i], ch_ramp[..., :3], float(ch_mix[i])) for i in range(4)], zones)
        else:
            tint = per_zone(colours)
        if overlay:
            if style != SOLID:
                tint = mix(tint, base, bare[..., None])
            tint = _overlay_blend(tint, ov_rgb, ov_w, ov_blend_mode)
            rough = mix(rough, ov_rough, ov_mat_w)
            out_metal_paint = mix(metalness, ov_metal, ov_mat_w)
        else:
            out_metal_paint = metalness
        if style != SOLID:
            tint = saturate(tint * mix(brightness, 1.0, fresh_edge(amount, bare, softness) * (1.0 - edge))[..., None])
        paint = tint * grunge[..., :3]
        out_colour = mix(_levels(tint, paint, metalness, wear), base, bare[..., None])
        out_rough = mix(np.minimum(1.0, rough + grunge_dulling), base_surface[..., 0], bare)
        out_metal = mix(out_metal_paint, base_surface[..., 1], bare)
        left = 1.0 - bare

    elif style in (ANODIZED, ANODIZED_MULTI, ANODIZED_AIR):
        paintable = masks[..., 0]
        ov_dur_scale = mix(1.0, ov_dur, ov_raw_w) if overlay else np.float32(1.0)
        if style == ANODIZED:
            hardness = np.float32(1.0)
            amount = exposure * ov_dur_scale
        else:
            hardness = mix(mix(_by_masks(durability, pattern), durability[2], masks[..., 1]), durability[3], masks[..., 2])
            amount = ((exposure + edge) if style == ANODIZED_MULTI else exposure) * ov_dur_scale * hardness
        softness = softness_value * hardness
        worn = worn_through(amount, softness)
        bare = np.maximum(1.0 - paintable, worn)
        scraped = fresh_edge(amount, worn, softness) * (1.0 - edge) * paintable
        if overlay:
            bare, _, ov_w, ov_mat_w = overlay_wear(bare, softness)
        rough = np.float32(p["g_flPaintRoughness"])
        if style == ANODIZED_MULTI:
            thin = 1.0 - np.minimum(1.0, pattern_alpha * 2.0)
            rough = mix(np.where(pattern_alpha >= 0.5, rough, thin * thin * 0.85 + 0.15), rough, np.maximum(np.maximum(masks[..., 1], masks[..., 2]), bare))
        rough = np.minimum(1.0, mix(rough, 0.35, scraped) + (1.0 - luma(grunge[..., :3])) * 0.2)
        out_rough = mix(base_surface[..., 0], rough, np.maximum(0.0, 1.0 - bare))
        out_metal = mix(paintable, base_surface[..., 1], bare)
        if style == ANODIZED:
            tint = colours[0]
        else:
            zones = pattern[..., :3] * paintable[..., None] if style == ANODIZED_MULTI else pattern[..., :3]
            tint = _by_masks(colours, zones)
            tint = mix(mix(tint, colours[2], masks[..., 1:2]), colours[3], masks[..., 2:3])
        tint = mix(mix(base, tint, paintable[..., None]), BARE_ANODIZED, scraped[..., None])
        if overlay:
            if style != ANODIZED:
                tint = mix(tint, base, bare[..., None])
            tint = _overlay_blend(tint, ov_rgb, ov_w, ov_blend_mode)
            out_rough = mix(out_rough, ov_rough, ov_mat_w)
            out_metal = mix(out_metal, ov_metal, ov_mat_w)
        boost = mix(brightness, 1.0, scraped)[..., None]
        tint = saturate(saturate(tint * boost) * boost)
        paint = tint * mix(grunge[..., :3], 1.0, scraped[..., None])
        out_colour = mix(_levels(tint, paint, np.float32(1.0), wear), base, bare[..., None])
        left = 1.0 - bare

    elif style == CUSTOM:
        hardness = np.maximum(0.0, smoothstep(0.0, 0.5, pattern_alpha))
        if overlay:
            hardness = np.maximum(0.0, hardness * mix(1.0, ov_dur, ov_raw_w))
        softness = softness_value * hardness
        bare = worn_through((exposure + edge) * hardness, softness)
        if overlay:
            bare, _, ov_w, ov_mat_w = overlay_wear(bare, softness)
        metalness = paint_metalness()
        rough = paint_roughness()
        tint = saturate(pattern[..., :3] * brightness)
        if overlay:
            tint = _overlay_blend(mix(tint, base, bare[..., None]), ov_rgb, ov_w, ov_blend_mode)
            rough = mix(rough, ov_rough, ov_mat_w)
            out_metal_paint = mix(metalness, ov_metal, ov_mat_w)
        else:
            out_metal_paint = metalness
        paint = tint * grunge[..., :3]
        out_colour = mix(_levels(tint, paint, metalness, wear), base, bare[..., None])
        out_rough = mix(np.minimum(1.0, rough + grunge_dulling), base_surface[..., 0], bare)
        out_metal = mix(out_metal_paint, base_surface[..., 1], bare)
        left = 1.0 - bare

    elif patina_age:
        out_colour, out_rough, out_metal, left = _patina_age_pass(
            style, p, textures, u, v, size, wear, cavity, masks[..., 0], wear_tex, grunge_raw,
            pattern, pattern_alpha, base, base_surface, colours, unpainted, scuff, exposure, edge,
            softness_value, worn_through, paint_roughness, paint_metalness,
        )

    else:  # ANTIQUED and GUNSMITH: metal that tarnishes instead of paint that chips
        patina_area = masks[..., 0]
        grime = saturate(grunge_raw[..., 0] * grunge_raw[..., 1] * grunge_raw[..., 2])
        if separate_inputs:
            scratched = smoothstep(0.2, 0.6, wear_tex * shade * (wear_tex * smoothstep(0.2, 0.3, cavity)) * wear)
            aged = smoothstep(0.5 * wear, 0.0, saturate(cavity_raw * 11.52 * shade - wear * grime * 2.0)) * wear
            clean = 1.0 - aged
            dirt = aged * 0.15 * wear
            grunge = np.maximum(grunge, saturate(scratched * 2.0)[..., None])
        else:
            scratched = smoothstep(0.1, 0.2, wear_tex * shade * cavity * cavity * wear)
            clean = smoothstep(0.0, 0.15, saturate(cavity * shade - wear * 0.1) - grime * 0.23 + 0.08)
            dirt = (1.0 - clean) * 0.15 * wear
        grunge_luma = luma(grunge[..., :3])
        grunge_wear = (1.0 - grunge[..., 3]) * wear
        rough = paint_roughness()
        metalness = paint_metalness()
        if case_hardening:
            ch_mask = masks[..., 1] if style == GUNSMITH else np.float32(1.0)
            ch_metal = mix(1.0, pattern_alpha, ch_mask) if style == GUNSMITH else ch_ramp[..., 3]
            rough = mix(rough, rough * (1.0 - 0.25 * luma(pattern[..., :3])), ch_mask)
        else:
            ch_metal = np.float32(1.0)
        bare = ov_bare = ov_w = ov_mat_w = None
        all_patina = patina_area > 0.99
        if style == GUNSMITH or overlay:
            hardness = np.maximum(0.0, smoothstep(0.0, 0.5, pattern_alpha))
            if overlay:
                hardness = np.maximum(0.0, hardness * mix(1.0, ov_dur, ov_raw_w))
            base_exp = ((np.minimum(unpainted, 1.0 - patina_area) + scuff) * (wear * 6.0 + 1.0)) if style == GUNSMITH else exposure
            amount = (base_exp + edge) * hardness
            softness = softness_value * hardness
            bare = np.where(all_patina, amount, smoothstep(0.58 - softness, 0.68 + softness, amount))
            if overlay:
                bare, ov_bare, ov_w, ov_mat_w = overlay_wear(bare, softness)
        if style == GUNSMITH:
            thin = 1.0 - np.minimum(1.0, pattern_alpha * 2.0)
            rough = mix(rough, np.where(pattern_alpha >= 0.5, rough, thin * thin * 0.85 + 0.15), patina_area)
        rough = saturate(rough * mix(1.0, 0.9, scratched) + (1.0 - grunge_luma) * wear * 0.05 + dirt + grunge_wear * 0.15)
        patina_metal = mix(ch_metal * mix(1.0, np.sqrt(clean * grunge[..., 3] * grunge_luma), wear), 1.0, scratched)
        rough = mix(np.minimum(1.0, rough + grunge_wear * wear * 0.5), rough, patina_area)
        tint = mix(pattern[..., :3], pattern[..., :3] * brightness, np.maximum(patina_area, float(p["g_nColorAdjustmentMode"]))[..., None])
        tarnish = mix(mix(colours[1], colours[3], math.sqrt(wear)), mix(colours[1], colours[2], wear), clean[..., None])
        patina = mix(tarnish * tint, colours[0] * luma(tint)[..., None], scratched[..., None])
        if overlay:
            patina = _overlay_blend(patina, ov_rgb, (1.0 - ov_bare) * ov_w, ov_blend_mode)
            non_pat = _overlay_blend(mix(saturate(tint), base, bare[..., None]) if style == GUNSMITH else base, ov_rgb, ov_w, ov_blend_mode)
            paint = mix(non_pat, patina, patina_area[..., None]) * grunge[..., :3]
            source = mix(non_pat, non_pat * colours[1], patina_area[..., None])
            level_metal = mix(metalness, ch_metal, patina_area) if style == GUNSMITH else ch_metal
            fitted = _levels(source, paint, level_metal, wear)
            out_colour = mix(fitted, base, (bare * (1.0 - patina_area))[..., None])
            if style == GUNSMITH:
                out_rough = mix(mix(base_surface[..., 0], rough, np.where(all_patina, 1.0, np.maximum(0.0, 1.0 - bare))), ov_rough, ov_mat_w)
                out_metal = mix(mix(mix(metalness, base_surface[..., 1], bare), patina_metal, patina_area), ov_metal, ov_mat_w)
            else:
                out_rough = mix(mix(base_surface[..., 0], rough, (bare <= 0.996) * patina_area), ov_rough, ov_mat_w)
                out_metal = mix(mix(base_surface[..., 1], patina_metal, patina_area), ov_metal, ov_mat_w)
            left = 1.0 - bare
        elif style == GUNSMITH:
            paint = mix(tint, patina, patina_area[..., None]) * grunge[..., :3]
            source = mix(tint, tint * colours[1], patina_area[..., None])
            fitted = _levels(source, paint, mix(metalness, ch_metal, patina_area), wear)
            out_colour = mix(fitted, base, (bare * (1.0 - patina_area))[..., None])
            out_rough = mix(base_surface[..., 0], rough, np.where(all_patina, 1.0, np.maximum(0.0, 1.0 - bare)))
            out_metal = mix(mix(metalness, base_surface[..., 1], bare), patina_metal, patina_area)
            left = 1.0 - bare
        else:
            paint = patina * grunge[..., :3]
            fitted = _levels(tint * colours[1], paint, ch_metal, wear)
            out_colour = mix(fitted, base, (1.0 - patina_area)[..., None])
            out_rough = mix(base_surface[..., 0], rough, (unpainted <= 0.996) * patina_area)
            out_metal = mix(base_surface[..., 1], patina_metal, patina_area)
            left = 1.0 - unpainted

    shape = (size, size)
    return {
        "colour": linear_to_srgb(np.broadcast_to(out_colour, shape + (3,))),
        "roughness": saturate(np.broadcast_to(out_rough, shape)).astype(np.float32),
        "metalness": saturate(np.broadcast_to(out_metal, shape)).astype(np.float32),
        "paint": saturate(np.broadcast_to(left, shape)).astype(np.float32),
    }
