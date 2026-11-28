"""The game's own sky HDRIs, extracted for any scene. No Blender imports here.

A map build already carries its sky: `core.maplights.material_sky` names the
texture a sky material samples, and Source2Viewer writes it as a 2:1
equirectangular `.exr`. This module does the same for every sky in the game, so
a fresh scene can be lit by Dust II or Rain Night without building a map.

Only the material and its texture are read, which takes about a second the
first time. The result is kept under `cache.version_dir / "hdri" / <key>/` and
reused from there without the converter.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from . import maplights

CATALOGUE_VERSION = 2  # 2: map-specific skies outside the skybox folder are listed too
EXTRACT_VERSION = 1
SKYBOX_FOLDER = "materials/skybox"
SKYBOX_EXTENSION = "vmat_c"
CATALOGUE_NAME = "catalog.json"
MANIFEST_NAME = "sky.json"
EXR_MAGIC = 0x01312F76  # OpenEXR's 0x76 0x2f 0x31 0x01, read as a little-endian int

# Skies shipped in `materials/skybox`, with the clean name each one deserves and
# the playable map whose `env_sky` names it. The map column was read from each
# map's entity lump on CS2 1.42.0.1: de_train's sky is `sky_overcast_01` and
# de_ancient's is `sky_hr_aztec_02_lighting`, while `sky_de_train03` belongs to
# de_overpass_vanity. The order is the order the panel lists them in.
SKY_NAMES: tuple[tuple[str, str, str, str], ...] = (
    ("dust2", "Dust II", "materials/skybox/sky_de_dust2.vmat", "de_dust2"),
    ("mirage", "Mirage", "materials/skybox/sky_de_mirage.vmat", "de_mirage"),
    ("inferno", "Inferno", "materials/skybox/test/s2_de_inferno_sky01.vmat", "de_inferno"),
    ("nuke", "Nuke", "materials/skybox/sky_de_nuke.vmat", "de_nuke"),
    ("overpass", "Overpass", "materials/skybox/sky_de_overpass_01.vmat", "de_overpass"),
    ("train", "Train", "materials/skybox/sky_overcast_01.vmat", "de_train"),
    ("ancient", "Ancient", "materials/skybox/sky_hr_aztec_02_lighting.vmat", "de_ancient"),
    ("anubis", "Anubis", "materials/skybox/sky_de_annubis.vmat", "de_anubis"),
    ("vertigo", "Vertigo", "materials/skybox/sky_de_vertigo.vmat", "de_vertigo"),
    ("italy", "Italy", "materials/skybox/cs_italy_s2_skybox_2_lighting.vmat", "cs_italy"),
    ("office", "Office", "materials/skybox/cs_office_45_0.vmat", "cs_office"),
    ("baggage", "Baggage", "materials/skybox/sky_ar_baggage_01.vmat", "ar_baggage"),
    ("cobblestone", "Cobblestone", "materials/skybox/sky_cbble_sunset.vmat", "de_cbble"),
    ("rain_night", "Rain Night", "materials/skybox/sky_rain_night_01.vmat", ""),
    ("aztec", "Aztec", "materials/skybox/sky_hr_aztec_02.vmat", ""),
    ("train_alt", "Train (Alt)", "materials/skybox/sky_de_train03.vmat", ""),
    ("ancient_vanity", "Ancient (Vanity)", "materials/skybox/sky_hr_aztec_02_v1.vmat", ""),
    ("italy_plain", "Italy (Plain)", "materials/skybox/cs_italy_s2_skybox_2.vmat", ""),
    ("rush", "Rush", "materials/skybox/rush_cloudy_01a.vmat", "rush_001"),
    ("convoy", "Convoy", "materials/skybox/convoy_sky.vmat", ""),
)

# Map-specific skies that live outside `materials/skybox`: de_cache names one,
# but its entity lump keeps `env_sky` in a child lump, so the map itself builds
# without a real sky. Cache's is too good to leave out.
MAP_SKY_NAMES: tuple[tuple[str, str, str, str], ...] = (
    ("cache", "Cache", "materials/de_cache/sky/de_cache_sky_001.vmat", "de_cache"),
)

# Materials in the skybox folder that are not a sky a scene can be lit by.
NOT_A_SKY: dict[str, str] = {
    "materials/skybox/sky_black.vmat": "a flat black sky",
    "materials/skybox/overpass_airplane_001.vmat": "an airplane prop",
    "materials/skybox/hr_dust_ground_sand_skybox.vmat": "a ground sand texture",
    "materials/skybox/sky_de_dust2vmat.vmat": "a leftover that samples Mirage's texture",
    "materials/skybox/cs_italy_s2_skybox_2_fog.vmat": "Italy's fog layer only",
    "materials/skybox/light_test_sky_default.vmat": "a lighting test sky",
    "materials/skybox/light_test_sky_sunset_light.vmat": "a lighting test sky",
    "materials/skybox/sky_csgo_cloudy01.vmat": "a cube map, which cannot be written as an equirectangular EXR",
    "materials/skybox/sky_cs15_daylight01_hdr.vmat": "a cube map, which cannot be written as an equirectangular EXR",
}
NOT_A_SKY_PREFIXES = ("materials/skybox/skymodel_",)  # reference sky models, not skies
# Map-specific skies live outside the skybox folder; they are worth listing anyway.
EXTRA_MATERIALS = frozenset(path for _key, _label, path, _map in MAP_SKY_NAMES)

DEFAULT_ROTATION = maplights.SKY_BASE_ROTATION  # Mapping Z degrees that lines the image up with Source
ACRONYMS = ("hdr", "cs", "csgo", "s2", "ii")


class SkyError(RuntimeError):
    """A sky that cannot be turned into an equirectangular HDRI."""


@dataclass(frozen=True)
class Skybox:
    """One sky in the game, before anything is extracted."""

    key: str  # "dust2", used for the cache folder and the panel
    label: str  # "Dust II"
    material: str  # "materials/skybox/sky_de_dust2.vmat"
    map: str = ""  # the playable map whose env_sky names it, if any


@dataclass(frozen=True)
class Sky:
    """An extracted sky, ready to become a Blender World."""

    key: str
    label: str
    material: str
    texture: str  # the compiled .vtex the material samples
    file: str  # the equirectangular .exr on disk
    width: int
    height: int
    exposure_bias: float  # g_flBrightnessExposureBias
    strength: float  # Background node strength at zero offset
    rotation: float  # Mapping node Z rotation in degrees at zero offset
    map: str = ""

    @property
    def is_equirectangular(self) -> bool:
        return self.height > 0 and self.width == self.height * 2


def catalogue(paths) -> list[Skybox]:
    """Every sky among the given package paths, named and in panel order.

    `paths` are package paths such as 'materials/skybox/sky_de_dust2.vmat_c';
    anything outside a skybox folder or named as not-a-sky is ignored.
    """
    known = {material: (key, label, game_map) for key, label, material, game_map in SKY_NAMES + MAP_SKY_NAMES}
    order = {key: index for index, (key, _label, _material, _map) in enumerate(SKY_NAMES + MAP_SKY_NAMES)}
    found: dict[str, Skybox] = {}
    for raw in paths:
        material = normalize_material(raw)
        if material in found or not _is_sky_material(material):
            continue
        named = known.get(material)
        if named is not None:
            key, label, game_map = named
        else:
            key, label, game_map = _invent_name(material)
        found[material] = Skybox(key=key, label=label, material=material, map=game_map)
    ordered = sorted(found.values(), key=lambda sky: (order.get(sky.key, len(order)), sky.label))
    skies: list[Skybox] = []
    used: set[str] = set()
    for sky in ordered:
        key = sky.key
        index = 2
        while key in used:
            key = f"{sky.key}_{index}"
            index += 1
        used.add(key)
        skies.append(sky if key == sky.key else Skybox(key=key, label=sky.label, material=sky.material, map=sky.map))
    return skies


def catalogue_from_package(converter, package: Path) -> list[Skybox]:
    """Discover the game's skies from the main package, without extracting any.

    The package listing covers `materials/skybox`; the handful of map-specific
    skies that live elsewhere are added by name, and a missing one only fails
    when somebody applies it.
    """
    entries = converter.list_package(Path(package), SKYBOX_FOLDER, SKYBOX_EXTENSION)
    paths = [entry.path for entry in entries]
    paths += [material + "_c" for material in sorted(EXTRA_MATERIALS)]
    return catalogue(paths)


def available(folder: Path, converter, package: Path) -> list[Skybox]:
    """The sky catalogue, read from `folder` when it was built before."""
    folder = Path(folder)
    stored = _read_catalogue(folder)
    if stored is not None:
        return stored
    skies = catalogue_from_package(converter, package)
    _write_catalogue(folder, skies)
    return skies


def extract(converter, package: Path, skybox: Skybox, folder: Path) -> Sky:
    """Export this sky's texture to `folder` and describe it for a Blender World.

    A second call for the same sky reads the small manifest beside the EXR, so
    the converter is not started again.
    """
    folder = Path(folder)
    cached = _read_sky(folder, skybox)
    if cached is not None:
        return cached

    dump = converter.dump(Path(package), skybox.material + "_c")
    found = maplights.material_sky(dump)
    if found is None:
        raise SkyError(f"The game's {skybox.label} sky has no equirectangular texture.")
    texture, exposure_bias = found
    path = _flatten(folder, converter.export_texture(Path(package), texture, folder, suffix=".exr"))
    width, height = exr_size(path) or (0, 0)
    if height <= 0 or width != height * 2:
        raise SkyError(
            f"The game's {skybox.label} sky is {width} by {height}, which is not a 2:1 equirectangular image."
        )
    sky = Sky(
        key=skybox.key,
        label=skybox.label,
        material=skybox.material,
        texture=texture,
        file=str(path),
        width=width,
        height=height,
        exposure_bias=exposure_bias,
        strength=default_strength(exposure_bias),
        rotation=DEFAULT_ROTATION,
        map=skybox.map,
    )
    _write_sky(folder, sky)
    return sky


def default_strength(exposure_bias: float) -> float:
    """The Background strength the material itself asks for, before any user offset."""
    return float(2.0 ** exposure_bias)


def _flatten(folder: Path, exported: Path) -> Path:
    """Keep the EXR directly in the sky's folder, not in the texture's package folders."""
    target = Path(folder) / Path(exported).name
    if Path(exported) == target:
        return target
    try:
        Path(exported).replace(target)
    except OSError:
        return Path(exported)
    return target


def normalize_material(path: str) -> str:
    """A package path as a material path: forward slashes, no compiled suffix."""
    text = str(path).replace("\\", "/").strip()
    return text.removesuffix("_c")


def exr_size(path: Path) -> tuple[int, int] | None:
    """The pixel size in an OpenEXR header, or None when the file is not an EXR."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 8 or struct.unpack_from("<I", data, 0)[0] != EXR_MAGIC:
        return None
    position = 8  # the magic and the version flags
    while position < len(data):
        end = data.find(b"\0", position)
        if end < 0:
            return None
        name = data[position:end].decode("latin-1")
        position = end + 1
        if not name:
            return None
        end = data.find(b"\0", position)
        if end < 0:
            return None
        kind = data[position:end].decode("latin-1")
        position = end + 1
        if position + 4 > len(data):
            return None
        size = struct.unpack_from("<i", data, position)[0]
        position += 4
        value = data[position : position + size]
        position += max(0, size)
        if name == "dataWindow" and kind == "box2i" and len(value) == 16:
            x_min, y_min, x_max, y_max = struct.unpack("<4i", value)
            return x_max - x_min + 1, y_max - y_min + 1
    return None


def label_for_map(name: str) -> str:
    """A playable map's clean name, or the map's own name when it has none."""
    for candidate in (name, name.removesuffix("_vanity")):
        for _key, label, _material, game_map in SKY_NAMES + MAP_SKY_NAMES:
            if game_map and game_map == candidate:
                return label
    return name


def key_for_material(material: str) -> str:
    """The catalogue key for a material path, so a map's sky world can point the dropdown at it."""
    wanted = normalize_material(material)
    for key, _label, path, _map in SKY_NAMES + MAP_SKY_NAMES:
        if path == wanted:
            return key
    return ""


def _is_sky_material(material: str) -> bool:
    if not material.endswith(".vmat"):
        return False
    if material in EXTRA_MATERIALS:
        return True
    if material in NOT_A_SKY or material.startswith(NOT_A_SKY_PREFIXES):
        return False
    return material.startswith(SKYBOX_FOLDER + "/")


def _invent_name(material: str) -> tuple[str, str, str]:
    """A clean key and label for a sky the table does not know yet."""
    stem = material.rsplit("/", 1)[-1].removesuffix(".vmat")
    words = [word for word in re.split(r"[^0-9A-Za-z]+", stem) if word]
    words = [word for word in words if word.lower() not in ("sky", "skybox", "vmat", "exr")]
    label = " ".join(_pretty(word) for word in words) or "Sky"
    key = re.sub(r"[^a-z0-9]+", "_", "_".join(words).lower()).strip("_") or "sky"
    return key, label, ""


def _pretty(word: str) -> str:
    if word.lower() in ACRONYMS:
        return word.upper()
    return word[:1].upper() + word[1:]


def _catalogue_file(folder: Path) -> Path:
    return Path(folder) / CATALOGUE_NAME


def _read_catalogue(folder: Path) -> list[Skybox] | None:
    try:
        data = json.loads(_catalogue_file(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != CATALOGUE_VERSION:
        return None
    skies = []
    for item in data.get("skies") or ():
        try:
            skies.append(Skybox(**item))
        except (TypeError, KeyError):
            return None
    return skies or None


def _write_catalogue(folder: Path, skies: list[Skybox]) -> None:
    payload = {"version": CATALOGUE_VERSION, "skies": [asdict(sky) for sky in skies]}
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        _catalogue_file(folder).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass  # the catalogue is rebuilt next time; extracting still works


def _read_sky(folder: Path, skybox: Skybox) -> Sky | None:
    try:
        data = json.loads((Path(folder) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != EXTRACT_VERSION:
        return None
    sky = data.get("sky")
    if not isinstance(sky, dict) or sky.get("material") != skybox.material or sky.get("key") != skybox.key:
        return None
    if not Path(str(sky.get("file", ""))).is_file():
        return None
    try:
        return Sky(**sky)
    except TypeError:
        return None


def _write_sky(folder: Path, sky: Sky) -> None:
    payload = {"version": EXTRACT_VERSION, "sky": asdict(sky)}
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / MANIFEST_NAME).write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
