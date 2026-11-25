"""What the installed game offers: agents, weapons, animation clips and maps. No Blender imports here."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import clipdata, proc
from .converter import Converter, PackageEntry

AGENT_PREFIX = "agents/models/"
CLIP_PREFIX = "animation/anims/"
# Third-person clips made for gameplay. Menu poses ('ui_anims') use the same
# skeleton; viewmodel, chicken and egg clips do not fit an agent.
WORLD_CLIP_PREFIX = "animation/anims/world/"
BASE_CLIP = "animation/anims/world/knife/_default_knife/idle_knife"
AGENT_CLIP_PREFIXES = (WORLD_CLIP_PREFIX, CLIP_PREFIX + "ui_anims/")
WEAPON_PREFIX = "weapons/models/"
CATALOG_VERSION = 3
# The game's English names: "CSGO_CustomPlayer_ctm_sas_variantf" and "SFUI_WPNHUD_AK47".
AGENT_NAME_PREFIX = "csgo_customplayer_"
WEAPON_NAME_PREFIX = "sfui_wpnhud_"
# Weapon name tokens spelled differently from the catalogue key.
WEAPON_NAME_TOKENS = {
    "m4a4": "m4a1",
    "elite": "elites",
    "cz75a": "cz75",
    "incendiarygrenade": "incgrenade",
    "knife_default_ct": "knife",
    "knife_default_t": "knife",
    "knife_bayonet": "knifebayonet",
    "knife_m9": "knifem9",
    "knife_flip": "knifeflip",
    "knife_gut": "knifegut",
    "knife_css": "knifecss",
    "knife_tactical": "knifetactical",
    "knife_falchion": "knife_falchion_advanced",
    "knife_karambit": "knifekaram",
    "knife_bowie": "knife_survival_bowie",
    "knife_navaja": "knife_gypsy_jackknife",
    "knife_talon": "knife_widowmaker",
}
SHORT_NAME_LIMIT = 32  # characters that fit a dropdown at a normal sidebar width


@dataclass(frozen=True)
class Agent:
    key: str  # "ctm_sas/ctm_sas_variantf"
    model_path: str  # "agents/models/ctm_sas/ctm_sas_variantf.vmdl_c"
    faction: str  # "ctm_sas"
    name: str = ""  # the game's English name, "" when it has none

    @property
    def side(self) -> str:
        if self.faction.startswith("ctm_"):
            return "CT"
        if self.faction.startswith("tm_"):
            return "T"
        return "Other"

    @property
    def label(self) -> str:
        return self.key.split("/")[-1]


def agent_name(key: str, english: dict[str, str]) -> str:
    """The game's English name for an agent key, or ''."""
    model = key.rsplit("/", 1)[-1].lower()
    return english.get(AGENT_NAME_PREFIX + model, "")


def agents_from_entries(entries: list[PackageEntry], english: dict[str, str] | None = None) -> list[Agent]:
    agents = []
    for entry in entries:
        path = entry.path
        if not (path.startswith(AGENT_PREFIX) and path.endswith(".vmdl_c")):
            continue
        parts = path[len(AGENT_PREFIX):].split("/")
        # agents/models/<faction>/<name>.vmdl_c only; 'shared' holds gloves and arms.
        if len(parts) != 2 or parts[0] == "shared":
            continue
        name = parts[1][: -len(".vmdl_c")]
        key = f"{parts[0]}/{name}"
        agents.append(Agent(key=key, model_path=path, faction=parts[0], name=agent_name(key, english or {})))
    return sorted(agents, key=lambda a: a.key)


@dataclass(frozen=True)
class Weapon:
    key: str  # "ak47", "knife_karambit"
    model_path: str  # "weapons/models/ak47/weapon_rif_ak47.vmdl_c"
    kind: str  # "rif", "pist", "smg", "snip", "shot", "mach", "knife", or "other"
    name: str = ""  # the game's English name, "" when it has none

    @property
    def label(self) -> str:
        return self.key.replace("_", " ")


def weapon_name(key: str, english: dict[str, str]) -> str:
    """The game's English name for a catalogue weapon key, or ''."""
    from .skins import item_name  # skins imports this module, so only when it is needed

    candidates = [WEAPON_NAME_TOKENS.get(key), key, item_name(key).removeprefix("weapon_")]
    for candidate in candidates:
        if not candidate:
            continue
        found = english.get(WEAPON_NAME_PREFIX + candidate.lower(), "")
        if found:
            return found
    return ""


def short_names(names: list[str], limit: int = SHORT_NAME_LIMIT) -> list[str]:
    """Dropdown labels for game names: the part before ' | ', when that fits and is unique.

    The full name stays for the tooltip. Two agents whose short forms would read
    the same keep their full names, so the list still tells them apart.
    """
    shortened = [_before_faction(name, limit) for name in names]
    counts: dict[str, int] = {}
    for name in shortened:
        counts[name] = counts.get(name, 0) + 1
    return [name if counts[short] > 1 else short for name, short in zip(names, shortened)]


def _before_faction(name: str, limit: int) -> str:
    short = name.split(" | ", 1)[0].strip() if " | " in name else name
    if len(short) <= limit:
        return short
    cut = short[: limit - 1].rsplit(" ", 1)[0] or short[: limit - 1]
    return cut + "…"


_WEAPON_KINDS = ("rif", "pist", "smg", "snip", "shot", "mach", "knife")
_WEAPON_SKIP = ("_mag", "_broken_glass")


def weapons_from_entries(entries: list[PackageEntry], english: dict[str, str] | None = None) -> list[Weapon]:
    weapons = []
    for entry in entries:
        path = entry.path
        if not (path.startswith(WEAPON_PREFIX) and path.endswith(".vmdl_c")):
            continue
        parts = path[len(WEAPON_PREFIX):].split("/")
        stem = parts[-1][: -len(".vmdl_c")]
        # Holdable models are '<folder>/weapon_*'; skip magazines, shells, shared parts and debris.
        if parts[0] == "shared" or not stem.startswith("weapon_") or stem.endswith(_WEAPON_SKIP):
            continue
        rest = stem[len("weapon_"):]
        kind, _, name = rest.partition("_")
        if kind not in _WEAPON_KINDS or not name:
            kind, name = "other", rest
        key = f"knife_{name}" if kind == "knife" else name
        weapons.append(Weapon(key=key, model_path=path, kind=kind, name=weapon_name(key, english or {})))
    return sorted(weapons, key=lambda w: (w.kind, w.key))


def clips_from_entries(entries: list[PackageEntry]) -> list[str]:
    clips = []
    for entry in entries:
        path = entry.path
        if path.startswith(CLIP_PREFIX) and path.endswith(".vnmclip_c") and "+" not in path:
            clips.append(path[: -len(".vnmclip_c")])
    return sorted(clips)


def package_entries(converter: Converter, package: Path, entries=None) -> list[PackageEntry]:
    """The package's file listing, read once and shared by the lists that need it.

    `entries` may be the listing itself or a function that reads it, so a cached
    list costs no converter run.
    """
    if callable(entries):
        return entries()
    if entries is not None:
        return entries
    return converter.list_package(package)


def build_catalog(converter: Converter, package: Path, entries=None) -> dict:
    from . import skins as skins_mod  # skins imports this module

    entries = package_entries(converter, package, entries)
    english = skins_mod.read_english(converter, package)
    agents = agents_from_entries(entries, english)
    clips = clips_from_entries(entries)
    weapons = weapons_from_entries(entries, english)
    return {
        "version": CATALOG_VERSION,
        "agents": [asdict(a) for a in agents],
        "weapons": [asdict(w) for w in weapons],
        "clips": clips,
    }


def load_or_build_catalog(converter: Converter, package: Path, cache_file: Path, entries=None) -> dict:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("version") == CATALOG_VERSION:
            return data
    except (OSError, ValueError):
        pass
    data = build_catalog(converter, package, entries)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = proc.temp_name(cache_file)
    tmp.write_text(json.dumps(data), encoding="utf-8")
    proc.replace_file(tmp, cache_file)
    return data


def catalog_agents(data: dict) -> list[Agent]:
    return [Agent(**a) for a in data.get("agents", [])]


def catalog_weapons(data: dict) -> list[Weapon]:
    return [Weapon(**w) for w in data.get("weapons", [])]


def clip_label(clip: str) -> str:
    """'animation/anims/world/rifle/rifle_ak47/run_n_ak47' -> 'world/rifle/rifle_ak47/run_n_ak47'."""
    return clip[len(CLIP_PREFIX):] if clip.startswith(CLIP_PREFIX) else clip


# Weapon kind -> the folder of third-person clips made for holding it.
_CLIP_FAMILY = {"knife": "knife", "pist": "pistol", "rif": "rifle", "smg": "rifle", "snip": "rifle", "shot": "rifle", "mach": "rifle"}
_EQUIPMENT = ("c4", "healthshot")
_HELD_LIKE_PISTOL = ("taser",)  # not named weapon_pist_*, but held with the pistol clips
SHARED_CLIP_FAMILY = "shared"  # deaths, flinches and jumps, played over any weapon


def clip_family(weapon: Weapon) -> str:
    """'rifle', 'pistol', 'knife', 'grenade' or 'equipment': which clips hold this weapon correctly."""
    if weapon.kind in _CLIP_FAMILY:
        return _CLIP_FAMILY[weapon.kind]
    if weapon.key in _HELD_LIKE_PISTOL:
        return "pistol"
    return "equipment" if weapon.key in _EQUIPMENT else "grenade"


def family_of_clip(clip: str) -> str:
    """'animation/anims/world/rifle/rifle_ak/run' or its label -> 'rifle'; '' outside the world clips."""
    parts = clip_label(clip).split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "world" else ""


def clip_fits(clip: str, weapon: Weapon | None) -> bool:
    return weapon is None or family_of_clip(clip) in (clip_family(weapon), SHARED_CLIP_FAMILY)


def weapon_skeleton_fits(weapon_key: str, skeleton_path: str) -> bool:
    """Whether a clip's weapon skeleton ('animation/skeletons/weapons/galil.vnmskel') is this weapon's.

    Skeletons are named after the weapon, sometimes shortened: galil for galilar,
    incendiary for incendiarygrenade.
    """
    name = skeleton_path.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0]
    return bool(name) and (weapon_key == name or weapon_key.startswith(name))


def clips_for_weapon(clips: list[str], weapon: Weapon | None) -> list[str]:
    """Clips for the held weapon: its own folder first (a karambit's own draw), then its
    family's default clips it does not replace, then the shared ones. Takes full clip paths."""
    if weapon is None:
        # No weapon: every clip made for an agent, without the first-person, chicken and egg ones.
        return [clip for clip in clips if clip.startswith(AGENT_CLIP_PREFIXES)]
    family = clip_family(weapon)
    base = f"{WORLD_CLIP_PREFIX}{family}/"
    folders = sorted({clipdata.folder_of(clip) for clip in clips if clip.startswith(base)})
    own = clipdata.weapon_folder(weapon.key, folders)
    return clipdata.clips_for_weapon(clips, f"{base}_default_{family}", own, WORLD_CLIP_PREFIX + SHARED_CLIP_FAMILY)


_CLIP_ORDER = ("world", "ui_anims")


def clip_order(clip: str) -> tuple[int, str]:
    """Gameplay clips first, then menu poses, then clips for other skeletons."""
    top = clip_label(clip).split("/", 1)[0]
    return (_CLIP_ORDER.index(top) if top in _CLIP_ORDER else len(_CLIP_ORDER), clip)


@dataclass(frozen=True)
class GameMap:
    name: str  # "de_dust2"
    package: str  # the map's own .vpk

    @property
    def world_path(self) -> str:
        return f"maps/{self.name}.vmap_c"

    @property
    def is_backdrop(self) -> bool:
        """Small scenes the game uses behind menus and previews, not playable maps."""
        return self.name.endswith("_vanity") or self.name.startswith("workshop_preview_")


_NOT_MAPS = ("graphics_settings", "lobby_mapveto")


def maps_in(maps_dir: Path) -> list[GameMap]:
    """Maps shipped with the game, playable ones first. Each map is one package in the maps folder."""
    if not maps_dir.is_dir():
        return []
    maps = [GameMap(p.stem, str(p)) for p in maps_dir.glob("*.vpk") if p.stem not in _NOT_MAPS]
    return sorted(maps, key=lambda m: (m.is_backdrop, m.name))