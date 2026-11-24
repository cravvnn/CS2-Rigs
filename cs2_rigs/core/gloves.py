"""Glove finishes: which glove model a finish is for, and the textures it paints. No Blender imports here.

Gloves are finished by a different shader from weapons. The glove's texture
atlas has a layer mask that picks one of four materials (leather, metal, fabric
and so on) and a tint id that picks one of eight colours. `compose` follows the
parts of that shader that show in a render: the tints, a pattern that replaces
one of them, each material's detail and its roughness and metalness, and wear
that exposes the edges. The game's exact shader is not known, so the result is
close to the game's look rather than identical to it.
"""

from __future__ import annotations

import os

import numpy as np

from . import composite
from .composite import fit, mix, sample, saturate, smoothstep, srgb_to_linear, uv_transform

VERSION = 1  # raise when results change, so textures composed earlier are not reused
SEPARATOR = " | "
RECIPE_FOLDER = "gloves/paints/"
LAYERS = 4
TINTS = 8
TINT_LEVELS = (18.0 / 255.0, 29.7 / 255.0)  # tint ids are stored in the mask's alpha: first level and step
ID_STEP = 32.5 / 255.0  # the same for the newer finishes, which keep the id in a texture's red channel

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
    """1 for finishes with a layer mask and per-material tints, 2 for the layered ones made for the newer shader."""
    return 2 if "g_tLayerId" in values else 1


def texture_names(values: dict) -> list[str]:
    """The textures of a resolved finish that `compose` reads, by shader name."""
    if generation(values) == 2:
        names = ["g_tLayerId", "g_tTintId", "g_tObjectProperties"]
        names += [f"g_t{kind}{i}" for i in range(1, LAYERS + 1) for kind in ("Surface", "Substrate", "Damage", "Grime")]
        if any(values.get(f"g_bId{i}Pattern") for i in range(1, TINTS + 1)):
            names.append("g_tPattern")
    else:
        names = ["g_tLayerMask", "g_tSurface", "g_tNoise"]
        names += [f"g_t{kind}{i}" for i in range(1, LAYERS + 1) for kind in ("Detail", "Grunge")]
        if values.get("F_PATTERN"):
            names.append("g_tPattern")
    return [name for name in names if values.get(name)]


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


def _tint(params, name) -> np.ndarray:
    return srgb_to_linear(_vector(params, name))


def _tinted(index, palette, default):
    """The colour for each pixel's tint index (1..8), `default` where the index is out of range."""
    out = np.broadcast_to(np.asarray(default, dtype=np.float32), index.shape + (3,)).copy()
    for number, colour in enumerate(palette, start=1):
        out[index == number] = colour
    return out


def _saturation(rgb, amount):
    grey = composite.luma(rgb)[..., None]
    return grey + (rgb - grey) * max(1.0 + min(amount, 0.3), 0.0)


def _tile(image, u, v, scale):
    return sample(image, u * scale, v * scale, composite.WRAP, composite.WRAP)


def _with_alpha(image):
    if image.shape[-1] == 3:
        return np.concatenate((image, np.ones_like(image[..., :1])), axis=-1)
    return image


def _layer_weights(rgb):
    """Weights of the four materials: the first where no channel is set, then red, green and blue."""
    second, third, fourth = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    first = saturate(1.0 - second - third - fourth)
    total = np.maximum(first + second + third + fourth, 1e-4)
    return [first / total, second / total, third / total, fourth / total]


def _pattern_colour(params, pattern, palette, u, v):
    """What the pattern paints where it replaces a tint.

    Most patterns are masks whose three channels pick colours from the palette;
    the rest are pictures that are used as they are.
    """
    first, second = uv_transform(
        _number(params, "g_fPatternTexCoordRotation"),
        _number(params, "g_fPatternTexCoordScale", 1.0),
        _vector(params, "g_fPatternTexCoordOffset", (0.0, 0.0)),
    )
    picked = sample(_with_alpha(pattern), first[0] * u + first[1] * v + first[2], second[0] * u + second[1] * v + second[2], composite.WRAP, composite.WRAP)
    if int(_number(params, "g_nPatternMode")) == 2:
        return srgb_to_linear(picked[..., :3])
    indices = [int(i) for i in params.get("g_vPatternPaletteIndices", (1, 1, 1, 1))][:4]
    colours = [palette[min(max(i, 1), TINTS) - 1][:3] for i in indices]
    zones = picked[..., :3]
    return mix(mix(mix(colours[0], colours[1], zones[..., 0:1]), colours[2], zones[..., 1:2]), colours[3], zones[..., 2:3])


def compose(values: dict, textures: dict, size: int, wear: float = 0.0) -> dict:
    """Colour, roughness and metalness for one glove of one finish.

    `values` is the resolved finish (shader values by their game names, textures
    as package paths) and `textures` maps the names `texture_names` lists to
    images. Returns `colour` (sRGB encoded), `roughness` and `metalness`, sized
    `size`, with row 0 at the top.
    """
    wear = float(min(max(wear, 0.0), 1.0))
    centres = (np.arange(size, dtype=np.float32) + 0.5) / size
    u, v = np.meshgrid(centres, centres)
    build = _newer if generation(values) == 2 else _older
    colour, rough, metal = build(values, textures, size, wear, u, v)
    shape = (size, size)
    return {
        "colour": composite.linear_to_srgb(np.broadcast_to(colour, shape + (3,))),
        "roughness": saturate(np.broadcast_to(rough, shape)).astype(np.float32),
        "metalness": saturate(np.broadcast_to(metal, shape)).astype(np.float32),
    }


def _older(p, textures, size, wear, u, v):
    """Finishes with a layer mask: the mask's colour channels pick the material, its alpha the tint."""
    mask = fit(textures["g_tLayerMask"], size)
    weights = _layer_weights(mask[..., :3])
    tint_index = np.clip(np.rint((mask[..., 3] - TINT_LEVELS[0]) / TINT_LEVELS[1]), 0, TINTS - 1).astype(np.int32) + 1
    palette = [_tint(p, f"g_vColorTint{i}")[:3] for i in range(1, TINTS + 1)]
    tint = _tinted(tint_index, palette, palette[0])
    if p.get("F_PATTERN") and "g_tPattern" in textures:
        replaced = tint_index == int(_number(p, "g_nPatternReplaceIndex", 1))
        if replaced.any():
            tint = np.where(replaced[..., None], _pattern_colour(p, textures["g_tPattern"], palette, u, v), tint)

    # The surface texture holds the glove's occlusion in green and its worn-first edges in blue.
    surface = fit(textures["g_tSurface"], size) if "g_tSurface" in textures else np.zeros((size, size, 4), dtype=np.float32)
    occlusion = saturate(surface[..., 1])
    edge = saturate(surface[..., 2])
    noise = _tile(textures["g_tNoise"], u, v, 3.0)[..., 0] if "g_tNoise" in textures else np.float32(0.5)
    progress = wear ** max(_number(p, "g_fWearExponent", 1.0), 0.05)

    colour = np.zeros((size, size, 3), dtype=np.float32)
    rough = np.zeros((size, size), dtype=np.float32)
    metal = np.zeros((size, size), dtype=np.float32)
    for number in range(1, LAYERS + 1):
        weight = weights[number - 1]
        if not weight.any():
            continue
        detail = _tile(textures[f"g_tDetail{number}"], u, v, _number(p, f"g_fDetailScale{number}", 1.0)) if f"g_tDetail{number}" in textures else np.full((size, size, 4), 0.5, dtype=np.float32)
        grunge = _tile(textures[f"g_tGrunge{number}"], u, v, _number(p, "g_fGrungeTexCoordScale", 1.0)) if f"g_tGrunge{number}" in textures else np.ones((size, size, 4), dtype=np.float32)
        black = _number(p, f"g_fDetailBlackPoint{number}")
        albedo = saturate((detail[..., 0] - black) / max(1.0 - black, 1e-3))
        dirt = 1.0 - _number(p, f"g_fDetailGrunge{number}") * (1.0 - grunge[..., 3])
        layer = tint * (albedo * dirt)[..., None]

        # How far worn through this material is: more at the edges, and patchy.
        low, high = (list(p.get(f"g_vDamageLevels{number}", (0.0, 1.0))) + [1.0, 1.0])[:2]
        power = max(_number(p, f"g_fCurvaturePower{number}", 1.0), 0.1)
        exposure = progress * 0.25 * (0.25 + noise + _number(p, f"g_fCurvatureWearBoost{number}") * edge ** (power * 0.25) * 2.0)
        damage = smoothstep(low, max(high, low + 1e-3), exposure * (0.5 + 0.5 * occlusion))

        worn = _saturation(layer, _number(p, f"g_fDamageSaturation{number}"))
        worn = saturate(worn + _number(p, f"g_fDamageBrightness{number}") * 0.5)
        bleach = saturate(progress * _number(p, f"g_fWearBleaching{number}") * 0.15)
        layer = mix(layer, saturate(worn * (1.0 + bleach)), damage[..., None])

        spread = 0.5 + (detail[..., 1] - 0.5) * (1.0 + _number(p, f"g_fDetailRoughnessContrast{number}"))
        layer_rough = saturate(spread * _number(p, f"g_fDetailRoughnessBrightness{number}", 1.0))
        layer_rough = mix(layer_rough, saturate(layer_rough * _number(p, f"g_fDamageRoughnessBrightness{number}", 1.0)), damage)
        layer_metal = mix(_number(p, f"g_fDetailMetalness{number}"), _number(p, f"g_fDamageMetalness{number}"), damage)

        colour += layer * weight[..., None]
        rough += layer_rough * weight
        metal += layer_metal * weight
    return colour, rough, metal


def _newer(p, textures, size, wear, u, v):
    """Finishes made for the layered shader: a surface over a substrate, tinted by an id texture, with an optional pattern."""
    weights = _layer_weights(fit(textures["g_tLayerId"], size)[..., :3])
    ids = np.rint(fit(textures["g_tTintId"], size)[..., 0] / ID_STEP).astype(np.int32) if "g_tTintId" in textures else np.zeros((size, size), dtype=np.int32)
    properties = fit(textures["g_tObjectProperties"], size) if "g_tObjectProperties" in textures else np.zeros((size, size, 4), dtype=np.float32)
    edge = saturate(properties[..., 1])
    progress = wear ** 0.8
    pattern = None
    if "g_tPattern" in textures:
        scale = _vector(p, "g_vPatternTexCoordScale", (1.0, 1.0))
        offset = _vector(p, "g_vPatternTexCoordOffset", (0.0, 0.0))
        pattern = sample(_with_alpha(textures["g_tPattern"]), u * scale[0] + offset[0], v * scale[1] + offset[1], composite.WRAP, composite.WRAP)

    colour = np.zeros((size, size, 3), dtype=np.float32)
    rough = np.zeros((size, size), dtype=np.float32)
    metal = np.zeros((size, size), dtype=np.float32)
    for number in range(1, LAYERS + 1):
        weight = weights[number - 1]
        if not weight.any():
            continue
        scale = float(_vector(p, f"g_vUvScale{number}", (1.0, 1.0))[0])
        surface = _tile(textures[f"g_tSurface{number}"], u, v, scale) if f"g_tSurface{number}" in textures else np.full((size, size, 4), 0.5, dtype=np.float32)
        base = srgb_to_linear(surface[..., :3]) * _tint(p, f"g_vSurfaceColorTint{number}")[:3]
        substrate = base
        if f"g_tSubstrate{number}" in textures:
            substrate = srgb_to_linear(_tile(textures[f"g_tSubstrate{number}"], u, v, scale)[..., :3]) * _tint(p, f"g_vSubstrateColorTint{number}")[:3]
        height = _tile(textures[f"g_tDamage{number}"], u, v, _number(p, f"g_fDamageUvScale{number}", 1.0))[..., 0] if f"g_tDamage{number}" in textures else np.float32(0.5)
        grime = _tile(textures[f"g_tGrime{number}"], u, v, _number(p, f"g_fGrimeUvScale{number}", 1.0)) if f"g_tGrime{number}" in textures else np.ones((size, size, 4), dtype=np.float32)

        # A tint id lays its colour over the surface, with the colour's alpha as cover, or the pattern.
        layer = base
        for index in range(1, TINTS + 1):
            where = (ids == index)[..., None]
            if not where.any():
                continue
            tint = _vector(p, f"g_vId{index}Color", (1.0, 1.0, 1.0, 0.0))
            if tint[3] > 0.0:
                layer = np.where(where, mix(layer, srgb_to_linear(tint[:3]), float(tint[3])), layer)
            if p.get(f"g_bId{index}Pattern") and pattern is not None:
                layer = np.where(where, mix(layer, srgb_to_linear(pattern[..., :3]), pattern[..., 3:4]), layer)

        low, high = (list(p.get(f"g_vDamageMinMax{number}", (0.0, 1.0))) + [1.0, 1.0])[:2]
        damage = smoothstep(low, max(high, low + 1e-3), (progress * (0.5 + height) + edge * progress) * 1.5)
        layer = mix(layer, substrate, damage[..., None])
        layer = mix(layer, layer * srgb_to_linear(grime[..., :3]) * 2.5, progress * 0.5)

        colour += layer * weight[..., None]
        rough += mix(0.55, 0.85, damage) * weight
        metal += _number(p, f"g_fBurnishingMetalness{number}") * weight
    return colour, rough, metal
