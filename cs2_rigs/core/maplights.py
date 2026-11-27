"""A CS2 map's sky and lights, read from its entity lump. No Blender imports here.

Two things the converted geometry does not carry:

- `env_sky` names the map's sky material. Its shader samples an HDR cubemap,
  which Source2Viewer writes as a 2:1 equirectangular `.exr`.
- `light_environment`, `light_omni2`, `light_rect` and `light_barn` place the
  sun and the map's lamps. Their intensities are physical and their angles are
  Source's pitch/yaw/roll.

The entity lump is KeyValues3, which `core.finish` already parses for materials,
so its parser is reused here.

Source and Blender axes
-----------------------
Source2Viewer's glTF export maps Source `(x, y, z)` to glTF `(y, z, x)` and
scales inches to metres. Blender's glTF importer maps glTF `(x, y, z)` to
Blender `(x, -z, y)`. Together:

    Blender position = (y, -x, z) * 0.0254

A Source entity angle is (pitch, yaw, roll), with positive pitch looking down.
`blender_light_axes` turns those into the light's local X/Y/Z axes in Blender,
where a Blender light emits along its local -Z and its local +Y is up.

The sky
-------
Source2Viewer samples the sky cubemap into an equirectangular image with
`u = 0` at Source +X and `u = 0.25` at Source -Y (Source's skybox face order is
+X, -X, +Y, -Y, +Z, -Z, with -Y the "front" face). Blender's equirectangular
environment maps `u = 0.5` to Blender +X, which is Source -Y. The two therefore
differ by a quarter turn: a Blender Mapping node rotated -90 degrees about Z
makes the image line up. `env_sky`'s yaw turns the sky with it, so the node
rotation is `-90 - yaw` degrees.

Light units
-----------
Source 2 stores every light's brightness in several representations and the
authored unit in `brightness_units` (0 EV, 1 lumens, 2 nits, 3 legacy,
4 candelas). All shipped CS2 maps populate `brightness_lumens`, so that is the
value used here. Blender light power is in watts, and the game's lights are
converted at the same 683 lumens per watt that Source2Viewer's glTF exporter
uses for `light_environment`:

    lamp watts = lumens * brightnessscale * LIGHT_GAIN / 683

`light_environment` is a sun: its `brightness` is the value the game's glTF
exporter already turns into a Blender sun strength, so it is used as-is. Lamps
are converted at 683 lumens per watt and then lifted by `LIGHT_GAIN` to stand in
for CS2's eye adaptation, which a fixed Blender render does not have.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import finish
from .converter import Converter, ConverterError

MANIFEST_VERSION = 1
UNIT = 0.0254  # metres per Source unit (one inch)
LUMENS_PER_WATT = 683.0  # the efficacy Source2Viewer uses for glTF lights
# CS2 exposes with eye adaptation and baked light probes; a fixed Blender render
# has neither. Lamps keep their lumens, but their watts are lifted by this much so
# interiors lit only by them are visible next to a 2.5 W/m2 sun. Outdoor accents
# stay far below the sun, so they do not blow out.
LIGHT_GAIN = 25.0
SKY_QUARTER_TURN = 0.25  # fraction of the image width between CS2's and Blender's u = 0
SKY_BASE_ROTATION = -360.0 * SKY_QUARTER_TURN  # degrees about Z for Blender's Mapping node
DEFAULT_TINT = (255.0, 255.0, 255.0)
DEFAULT_ANGULAR_DIAMETER = 0.53  # degrees, the sun seen from Earth

SUN = "light_environment"
LIGHT_CLASSES = (
    "light_environment",
    "light_omni",
    "light_omni2",
    "light_rect",
    "light_barn",
    "light_spot",
    "light_ortho",
    "light_dynamic",
    "light_directional",
)
AREA_CLASSES = ("light_rect", "light_ortho")
SPOT_CLASSES = ("light_barn", "light_spot")
SUN_CLASSES = ("light_environment", "light_directional")

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class Sky:
    """The map's real sky, ready for a Blender World."""

    material: str  # "materials/skybox/sky_de_dust2.vmat"
    texture: str  # "materials/skybox/sky_de_dust2_exr_908a35ba.vtex"
    file: str  # the exported equirectangular .exr
    strength: float  # Background node strength
    tint: tuple[float, float, float]  # linear RGB
    rotation: float  # Mapping node Z rotation, degrees
    yaw: float  # env_sky's own yaw, degrees
    exposure_bias: float  # g_flBrightnessExposureBias


@dataclass(frozen=True)
class Light:
    """One map light, calibrated for Blender."""

    classname: str
    name: str
    kind: str  # "sun", "point", "area" or "spot"
    origin: tuple[float, float, float]  # Source units
    angles: tuple[float, float, float]  # pitch, yaw, roll in degrees
    energy: float  # sun: W/m2; lamps: W
    color: tuple[float, float, float]  # linear RGB
    size: tuple[float, float] = (0.0, 0.0)  # metres, area lights and soft sources
    spot_size: float = 0.0  # full cone in degrees
    spot_blend: float = 0.0  # 0 hard edge, 1 fully soft
    cutoff: float = 0.0  # metres; 0 means inverse-square with no cutoff
    shadow: bool = True
    angular_diameter: float = DEFAULT_ANGULAR_DIAMETER  # sun, degrees
    area_shape: int = 0  # light_rect's shape value


@dataclass
class Extras:
    sky: Sky | None
    lights: list[Light]
    entities: int
    ignored: int  # light entities that were disabled or carried no energy

    def as_dict(self) -> dict:
        return {
            "version": MANIFEST_VERSION,
            "sky": asdict(self.sky) if self.sky is not None else None,
            "lights": [asdict(light) for light in self.lights],
            "light_gain": LIGHT_GAIN,
            "entities": self.entities,
            "ignored": self.ignored,
        }


def parse_entities(text: str) -> list[dict]:
    """The `values` block of every entity in a Source2Viewer dump of a .vents_c file."""
    body = _data_block(text)
    if body is None:
        return []
    try:
        parsed = finish.parse_kv3(body)
    except ValueError:
        return []
    entities = []
    for entry in parsed.get("m_entityKeyValues") or ():
        values = (entry.get("keyValues3Data") or {}).get("values")
        if isinstance(values, dict):
            entities.append(values)
    return entities


def sky_source(entities: list[dict]) -> dict | None:
    """The enabled `env_sky` the sun points at, or the first enabled one."""
    skies = [entity for entity in entities if entity.get("classname") == "env_sky" and _enabled(entity)]
    if not skies:
        return None
    sun = next((entity for entity in entities if entity.get("classname") == SUN), None)
    wanted = _text(sun.get("skytexture")) if sun is not None else ""
    chosen = next((entity for entity in skies if wanted and _text(entity.get("targetname")) == wanted), skies[0])
    material = _text(chosen.get("skyname"))
    if not material:
        return None
    return {
        "material": material,
        "tint": tuple(_srgb_to_linear(channel / 255.0) for channel in _vector(chosen.get("tint_color"), DEFAULT_TINT)),
        "brightness": _number(chosen.get("brightnessscale"), 1.0),
        "yaw": _vector(chosen.get("angles"))[1],
        "skyintensity": _number(sun.get("skyintensity"), 1.0) if sun is not None else 1.0,
    }


def material_sky(text: str) -> tuple[str, float] | None:
    """The sky texture and exposure bias a compiled sky material names."""
    body = _data_block(text)
    if body is None:
        return None
    try:
        parsed = finish.parse_kv3(body)
    except ValueError:
        return None
    values = finish.material_values(parsed)
    texture = values.get("g_tSkyTexture") or values.get("g_tSkyTexture0")
    if not isinstance(texture, str) or not texture:
        return None
    return texture, _number(values.get("g_flBrightnessExposureBias"), 0.0)


def make_sky(source: dict, texture: str, file: str, exposure_bias: float) -> Sky:
    """Calibrate an `env_sky` and its material for Blender."""
    strength = source["brightness"] * (2.0 ** exposure_bias) * source["skyintensity"]
    return Sky(
        material=source["material"],
        texture=texture,
        file=str(file),
        strength=max(0.0, strength),
        tint=source["tint"],
        rotation=sky_rotation(source["yaw"]),
        yaw=source["yaw"],
        exposure_bias=exposure_bias,
    )


def sky_rotation(yaw: float) -> float:
    """The Mapping node's Z rotation in degrees for an `env_sky` yaw."""
    return SKY_BASE_ROTATION - yaw


def lights_from_entities(entities: list[dict]) -> list[Light]:
    """Every enabled lamp and sun in the entity lump, calibrated."""
    lights = []
    for entity in entities:
        classname = entity.get("classname")
        if classname not in LIGHT_CLASSES or not _enabled(entity):
            continue
        light = _light(entity, classname)
        if light is not None:
            lights.append(light)
    return lights


def _light(entity: dict, classname: str) -> Light | None:
    kind = _kind(classname, entity)
    origin = _vector(entity.get("origin"))
    angles = _vector(entity.get("angles"))
    size = _size(entity)
    spot_size, spot_blend = _spot(classname, entity, size)
    energy = _energy(classname, entity, kind, size)
    if energy <= 0.0:
        return None
    distance = entity.get("range") or entity.get("distance")
    return Light(
        classname=classname,
        name=_text(entity.get("targetname")) or classname,
        kind=kind,
        origin=origin,
        angles=angles,
        energy=energy,
        color=_color(entity),
        size=size,
        spot_size=spot_size,
        spot_blend=spot_blend,
        cutoff=max(0.0, abs(_number(distance, 0.0)) * UNIT),
        shadow=_shadow(entity),
        angular_diameter=max(0.0, _number(entity.get("angulardiameter"), DEFAULT_ANGULAR_DIAMETER)),
        area_shape=int(_number(entity.get("shape"), 0.0)),
    )


def _kind(classname: str, entity: dict) -> str:
    if classname in SUN_CLASSES:
        return "sun"
    if classname in AREA_CLASSES:
        return "area"
    if classname in SPOT_CLASSES:
        return "spot"
    if classname == "light_omni2" and _number(entity.get("outer_angle"), 180.0) < 179.5:
        return "spot"
    return "point"


def _energy(classname: str, entity: dict, kind: str, size: tuple[float, float]) -> float:
    scale = _number(entity.get("brightnessscale"), 1.0)
    if kind == "sun":
        return max(0.0, _number(entity.get("brightness"), 0.0)) * scale
    lumens = _number(entity.get("brightness_lumens"), 0.0)
    if lumens <= 0.0:
        candelas = _number(entity.get("brightness_candelas"), 0.0)
        if candelas > 0.0:
            lumens = candelas * 4.0 * math.pi
    if lumens <= 0.0:
        nits = _number(entity.get("brightness_nits"), 0.0)
        if nits > 0.0:
            area = max(size[0] * size[1], 0.01)
            lumens = nits * area * math.pi
    if lumens <= 0.0:
        legacy = _number(entity.get("brightness_legacy"), 0.0)
        if legacy > 0.0:
            lumens = legacy * 100.0  # only reached when the other three are missing
    return max(0.0, lumens * scale / LUMENS_PER_WATT * LIGHT_GAIN)


def _size(entity: dict) -> tuple[float, float]:
    params = _vector(entity.get("size_params"))
    return (
        min(100.0, max(0.01, abs(params[0]) * UNIT)),
        min(100.0, max(0.01, abs(params[1]) * UNIT)),
    )


def _spot(classname: str, entity: dict, size: tuple[float, float]) -> tuple[float, float]:
    if classname == "light_barn":
        # Barn doors shape a rectangular cone whose width follows the emitter
        # size and the light's range.
        radius = max(size) * 0.5
        distance = max(0.1, abs(_number(entity.get("range"), 1.0)) * UNIT)
        outer = math.degrees(2.0 * math.atan2(radius, distance))
        softness = max(_number(entity.get("soft_x"), 0.5), _number(entity.get("soft_y"), 0.5))
        return _clamp_cone(outer), max(0.0, min(1.0, softness))
    inner = _number(entity.get("inner_angle", entity.get("innercone")), 0.0)
    outer = _number(entity.get("outer_angle", entity.get("outercone")), 0.0)
    if outer <= 0.0:
        outer = 45.0
    blend = 0.0
    if inner > 0.0:
        blend = 1.0 - min(1.0, inner / outer)
    return _clamp_cone(outer), max(0.0, min(1.0, blend))


def _clamp_cone(degrees: float) -> float:
    return max(1.0, min(179.0, degrees))


def _color(entity: dict) -> tuple[float, float, float]:
    if int(_number(entity.get("colormode"), 0.0)) == 1:
        return colour_temperature(_number(entity.get("colortemperature"), 6600.0))
    channels = _vector(entity.get("color"), DEFAULT_TINT)
    return tuple(_srgb_to_linear(max(0.0, min(255.0, channel)) / 255.0) for channel in channels)


def colour_temperature(kelvin: float) -> tuple[float, float, float]:
    """A black-body colour as linear RGB, using the usual Tanner Helland curve."""
    temperature = max(kelvin, 1000.0) / 100.0
    if temperature <= 66.0:
        red = 1.0
        green = (99.4708025861 * math.log(temperature) - 161.1195681661) / 255.0
        blue = 0.0 if temperature <= 19.0 else (138.5177312231 * math.log(temperature - 10.0) - 305.0447927307) / 255.0
    else:
        red = 329.698727446 * (temperature - 60.0) ** -0.1332047592 / 255.0
        green = 288.1221695283 * (temperature - 60.0) ** -0.0755148492 / 255.0
        blue = 1.0
    return tuple(_srgb_to_linear(max(0.0, min(1.0, channel))) for channel in (red, green, blue))


def blender_position(origin: tuple[float, float, float]) -> tuple[float, float, float]:
    """A Source entity origin in Blender metres."""
    x, y, z = origin
    return (y * UNIT, -x * UNIT, z * UNIT)


def blender_light_axes(angles: tuple[float, float, float]) -> tuple[tuple[float, float, float], ...]:
    """The light's local X, Y and Z axes in Blender for Source pitch/yaw/roll.

    A Blender light emits along local -Z and stands up along local +Y, so Z is
    the negated Source forward vector and X is Y cross Z.
    """
    forward, up = _source_forward_up(angles)
    z = _blender_vector(tuple(-component for component in forward))
    y = _blender_vector(up)
    x = (y[1] * z[2] - y[2] * z[1], y[2] * z[0] - y[0] * z[2], y[0] * z[1] - y[1] * z[0])
    return x, y, z


def _source_forward_up(angles: tuple[float, float, float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    pitch, yaw, roll = (math.radians(angle) for angle in angles)
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    forward = (cp * cy, cp * sy, -sp)
    up = (cr * sp * cy + sr * sy, cr * sp * sy - sr * cy, cr * cp)
    return forward, up


def _blender_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    return (y, -x, z)


def extract(
    converter: Converter,
    map_package: Path,
    main_package: Path,
    map_name: str,
    texture_dir: Path,
) -> Extras:
    """Read a map's sky and lights, exporting its sky texture into `texture_dir`."""
    text = converter.dump(Path(map_package), f"maps/{map_name}/entities/default_ents.vents_c")
    entities = parse_entities(text)
    source = sky_source(entities)
    sky = None
    if source is not None:
        texture = None
        exposure_bias = 0.0
        path = source["material"].replace("\\", "/").removesuffix("_c")
        if path.endswith(".vmat"):
            dump = _dump_any(converter, (Path(main_package), Path(map_package)), path + "_c")
            found = material_sky(dump) if dump is not None else None
            if found is not None:
                texture, exposure_bias = found
        elif path.endswith(".vtex"):
            texture = path
        if texture is not None:
            texture = texture.replace("\\", "/")
            exported = _export_any(converter, (Path(main_package), Path(map_package)), texture, Path(texture_dir))
            if exported is not None:
                sky = make_sky(source, texture, exported, exposure_bias)
    lights = lights_from_entities(entities)
    light_entities = sum(1 for entity in entities if entity.get("classname") in LIGHT_CLASSES)
    return Extras(sky=sky, lights=lights, entities=len(entities), ignored=max(0, light_entities - len(lights)))


def _dump_any(converter: Converter, packages: tuple[Path, ...], path: str) -> str | None:
    for package in packages:
        try:
            text = converter.dump(Path(package), path)
        except ConverterError:
            continue
        # A package without the file prints a message but still exits cleanly,
        # so an empty dump is not a successful match.
        if text and "--- Data for block" in text:
            return text
    return None


def _export_any(converter: Converter, packages: tuple[Path, ...], path: str, out_dir: Path) -> Path | None:
    compiled = path if path.endswith("_c") else path + "_c"
    for package in packages:
        try:
            return converter.export_texture(Path(package), compiled, Path(out_dir), suffix=".exr")
        except ConverterError:
            continue
    return None


def _data_block(text: str) -> str | None:
    """The KeyValues3 text of a converter dump's DATA block, without its comment."""
    body = text.partition('--- Data for block "DATA" ---')[2]
    if not body:
        return None
    body = body.split("--- Data for block", 1)[0]
    body = body.partition("-->")[2] or body
    start = body.find("{")
    return body[start:] if start >= 0 else None


def _enabled(entity: dict) -> bool:
    return entity.get("enabled") is not False and entity.get("StartDisabled") is not True


def _shadow(entity: dict) -> bool:
    return _number(entity.get("castshadows"), 1.0) > 0.0


def _text(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.removeprefix("[PR#]").strip()


def _vector(value, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """A Source vector, written as a list or as a space-separated string."""
    if isinstance(value, (list, tuple)):
        parts = list(value[:3])
    elif isinstance(value, str):
        parts = _NUMBER.findall(value.removeprefix("[PR#]"))
    else:
        return default
    if len(parts) != 3:
        return default
    try:
        return tuple(float(part) for part in parts)
    except (TypeError, ValueError):
        return default


def _number(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _srgb_to_linear(channel: float) -> float:
    channel = max(0.0, min(1.0, channel))
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
