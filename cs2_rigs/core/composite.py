"""Build a finished weapon's textures the way the game does. No Blender imports here.

The game paints a finish onto a weapon by rendering two textures, colour and
roughness/metalness, from the weapon's own textures, the finish's pattern and
colours, and shared wear and grunge textures. `compose` follows that maths for
the nine paint styles, for weapons with the older packed inputs and for the
current ones.

Images are float32 arrays shaped (height, width, channels) with row 0 at the
top, holding the values stored in the file (0..1, not yet linearised).

Not reproduced: overlays, halftone spray, masked spray, case hardening and
patina age, which only newer finishes use.
"""

from __future__ import annotations

import math

import numpy as np

VERSION = 3  # raise when results change, so textures composed earlier are not reused
SOLID, HYDROGRAPHIC, SPRAY, ANODIZED, ANODIZED_MULTI, ANODIZED_AIR, CUSTOM, ANTIQUED, GUNSMITH = range(9)
POSITION_STYLES = (SPRAY, ANODIZED_AIR)  # pattern projected from the weapon's shape, not its layout
PATTERN_STYLES = (HYDROGRAPHIC, SPRAY, ANODIZED_MULTI, ANODIZED_AIR, CUSTOM, ANTIQUED, GUNSMITH)
COLOUR_PATTERN_STYLES = (CUSTOM, ANTIQUED, GUNSMITH)  # pattern is a picture, not a mask
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
}
for _layer in ("Pattern", "Wear", "Grunge"):
    DEFAULTS[f"g_fl{_layer}TexCoordScale"] = 1.0
    DEFAULTS[f"g_fl{_layer}TexCoordRotation"] = 0.0
    DEFAULTS[f"g_v{_layer}TexCoordOffset"] = (0.0, 0.0)
STYLE_DEFAULTS = {ANODIZED_MULTI: {"g_flPaintRoughness": 0.2}, ANODIZED_AIR: {"g_flPaintRoughness": 0.2}}

PAINT_LEVELS = (0.045, -1.4427 * math.log(1 - 0.6), 1.0)
METALLIC_PAINT_LEVELS = (0.08, -1.4427 * math.log(1 - 0.6), 1.0)
LUMA = np.array((0.2125, 0.7154, 0.0721), dtype=np.float32)
BARE_ANODIZED = np.array((0.38, 0.37, 0.35), dtype=np.float32)


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


def compose(style, params, textures, size, wear=0.0, separate_inputs=False):
    """Colour and surface textures for one finish on one weapon.

    `params` holds the finish's shader values by their game names, with the
    weapon's own `g_flUvScale1` and `g_flWeaponLength1` merged in. `textures`
    maps game texture names to images: the weapon's `g_tColor`, `g_tMetalness`,
    `g_tAmbientOcclusion`, `g_tMasks`, `g_tPosition` and `g_tSurface`, the
    shared `g_tWear` and `g_tGrunge`, and the finish's `g_tPattern` and
    `g_tPaintRoughness` and `g_tPaintMetalness` where the finish uses them. `separate_inputs` is set for
    weapons whose inputs come as separate channels (the current models).

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

    def layer_rows(layer, by_length=False):
        if p["g_bIgnoreWeaponSizeScale"]:
            fitted = 1.0
        else:
            fitted = p["g_flWeaponLength1"] / 36.0 if by_length else p["g_flUvScale1"]
        return uv_transform(p[f"g_fl{layer}TexCoordRotation"], p[f"g_fl{layer}TexCoordScale"] * fitted, p[f"g_v{layer}TexCoordOffset"])

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
    low, high = (0.56, 0.74) if separate_inputs and style != GUNSMITH else (0.58, 0.68)

    def worn_through(amount, softness):
        return smoothstep(low - softness, high + softness, amount)

    def fresh_edge(amount, bare, softness):
        """0 where the paint is pristine, which is where brightening applies."""
        if separate_inputs:
            return smoothstep(0.53 - softness, 0.72 + softness, amount)
        return smoothstep(0.0, 0.01, bare)

    pattern = None
    if style in PATTERN_STYLES:
        modes = (int(p["g_nPatternTextureHorizontalSampling"]), int(p["g_nPatternTextureVerticalSampling"]))
        rows = layer_rows("Pattern", by_length)
        image = textures["g_tPattern"]
        if image.shape[-1] == 3:
            image = np.concatenate((image, np.ones_like(image[..., :1])), axis=-1)
        if style in POSITION_STYLES:
            position = weapon("g_tPosition", 3, (0.5, 0.5, 0.5)) * 2.0
            facing = weapon("g_tSurface", 3, (0.5, 0.5, 1.0))
            if separate_inputs and p["g_bBiasSpray"]:
                facing = linear_to_srgb(facing)
            facing = facing * 2.0 - 1.0
            facing = facing / np.maximum(np.linalg.norm(facing, axis=-1, keepdims=True), 1e-6)
            x, y, z = position[..., 0], position[..., 1], position[..., 2]
            side = sample(image, *_place(rows, y, z), *modes)
            top = sample(image, *_place(rows, x, z), *modes)
            front = sample(image, *_place(rows, y, x), *modes)
            blend_top, blend_front = p["g_vSprayBlend"][0], p["g_vSprayBlend"][1]
            pattern = mix(mix(side, top, (blend_top * np.abs(facing[..., 1]) ** 7)[..., None]), front, (blend_front * np.abs(facing[..., 2]) ** 7)[..., None])
        else:
            pattern = sample(image, *_place(rows, u, v), *modes)
        if style in COLOUR_PATTERN_STYLES:
            pattern = np.concatenate((srgb_to_linear(pattern[..., :3]), pattern[..., 3:4]), axis=-1)
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
        if style == SOLID:
            zones = masks
            hardness = _by_masks(durability, zones)
            amount = exposure * hardness
        elif style == HYDROGRAPHIC:
            hardness = mix(mix(_by_masks(durability, pattern), durability[2], masks[..., 1]), durability[3], masks[..., 2])
            amount = (exposure + edge) * np.maximum(saturate(masks[..., 1] + masks[..., 2]), smoothstep(0.0, 0.5, pattern_alpha)) * hardness
        else:
            hardness = _by_masks(durability, pattern)
            amount = exposure * hardness
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

        def per_zone(values):
            picked = _by_masks(values, zones)
            if style == HYDROGRAPHIC:
                third_value, fourth_value = (np.asarray(x, dtype=np.float32) for x in values[2:4])
                extra = masks[..., None, 1:3] if third_value.ndim else masks[..., 1:3]
                picked = mix(mix(picked, third_value, extra[..., 0]), fourth_value, extra[..., 1])
            return picked

        metalness = per_zone(p["g_vPaintMetalness"])
        rough = per_zone(p["g_vPaintRoughness"]) if p["F_ROUGHNESS_PER_COLOR"] else np.float32(p["g_flPaintRoughness"])
        tint = per_zone(colours)
        if style != SOLID:
            tint = saturate(tint * mix(brightness, 1.0, fresh_edge(amount, bare, softness) * (1.0 - edge))[..., None])
        paint = tint * grunge[..., :3]
        out_colour = mix(_levels(tint, paint, metalness, wear), base, bare[..., None])
        out_rough = mix(np.minimum(1.0, rough + grunge_dulling), base_surface[..., 0], bare)
        out_metal = mix(metalness, base_surface[..., 1], bare)
        left = 1.0 - bare

    elif style in (ANODIZED, ANODIZED_MULTI, ANODIZED_AIR):
        paintable = masks[..., 0]
        if style == ANODIZED:
            hardness = np.float32(1.0)
            amount = exposure
        else:
            hardness = mix(mix(_by_masks(durability, pattern), durability[2], masks[..., 1]), durability[3], masks[..., 2])
            amount = (exposure + edge) * hardness if style == ANODIZED_MULTI else exposure * hardness
        softness = softness_value * hardness
        worn = worn_through(amount, softness)
        bare = np.maximum(1.0 - paintable, worn)
        scraped = fresh_edge(amount, worn, softness) * (1.0 - edge) * paintable
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
        boost = mix(brightness, 1.0, scraped)[..., None]
        tint = saturate(saturate(mix(mix(base, tint, paintable[..., None]), BARE_ANODIZED, scraped[..., None]) * boost) * boost)
        paint = tint * mix(grunge[..., :3], 1.0, scraped[..., None])
        out_colour = mix(_levels(tint, paint, np.float32(1.0), wear), base, bare[..., None])
        left = 1.0 - bare

    elif style == CUSTOM:
        hardness = np.maximum(0.0, smoothstep(0.0, 0.5, pattern_alpha))
        bare = worn_through((exposure + edge) * hardness, softness_value * hardness)
        metalness = paint_metalness()
        tint = saturate(pattern[..., :3] * brightness)
        paint = tint * grunge[..., :3]
        out_colour = mix(_levels(tint, paint, metalness, wear), base, bare[..., None])
        out_rough = mix(np.minimum(1.0, paint_roughness() + grunge_dulling), base_surface[..., 0], bare)
        out_metal = mix(metalness, base_surface[..., 1], bare)
        left = 1.0 - bare

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
        if style == GUNSMITH:
            hardness = np.maximum(0.0, smoothstep(0.0, 0.5, pattern_alpha))
            amount = ((np.minimum(unpainted, 1.0 - patina_area) + scuff) * (wear * 6.0 + 1.0) + edge) * hardness
            all_patina = patina_area > 0.99
            bare = np.where(all_patina, amount, worn_through(amount, softness_value * hardness))
            thin = 1.0 - np.minimum(1.0, pattern_alpha * 2.0)
            rough = mix(rough, np.where(pattern_alpha >= 0.5, rough, thin * thin * 0.85 + 0.15), patina_area)
        rough = saturate(rough * mix(1.0, 0.9, scratched) + (1.0 - grunge_luma) * wear * 0.05 + dirt + grunge_wear * 0.15)
        patina_metal = mix(mix(1.0, np.sqrt(clean * grunge[..., 3] * grunge_luma), wear), 1.0, scratched)
        rough = mix(np.minimum(1.0, rough + grunge_wear * wear * 0.5), rough, patina_area)
        tint = mix(pattern[..., :3], pattern[..., :3] * brightness, np.maximum(patina_area, float(p["g_nColorAdjustmentMode"]))[..., None])
        tarnish = mix(mix(colours[1], colours[3], math.sqrt(wear)), mix(colours[1], colours[2], wear), clean[..., None])
        patina = mix(tarnish * tint, colours[0] * luma(tint)[..., None], scratched[..., None])
        if style == GUNSMITH:
            paint = mix(tint, patina, patina_area[..., None]) * grunge[..., :3]
            source = mix(tint, tint * colours[1], patina_area[..., None])
            fitted = _levels(source, paint, mix(metalness, 1.0, patina_area), wear)
            out_colour = mix(fitted, base, (bare * (1.0 - patina_area))[..., None])
            out_rough = mix(base_surface[..., 0], rough, np.where(all_patina, 1.0, np.maximum(0.0, 1.0 - bare)))
            out_metal = mix(mix(metalness, base_surface[..., 1], bare), patina_metal, patina_area)
            left = 1.0 - bare
        else:
            paint = patina * grunge[..., :3]
            fitted = _levels(tint * colours[1], paint, np.float32(1.0), wear)
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
