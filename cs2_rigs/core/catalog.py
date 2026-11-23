"""What the installed game offers: agents, weapons, animation clips and maps. No Blender imports here."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .converter import Converter, PackageEntry

AGENT_PREFIX = "agents/models/"
CLIP_PREFIX = "animation/anims/"
# Third-person clips. Viewmodel clips target the first-person skeleton and UI
# clips are menu poses; both stay out of the default browser.
WORLD_CLIP_PREFIX = "animation/anims/world/"
BASE_CLIP = "animation/anims/world/knife/_default_knife/idle_knife"
WEAPON_PREFIX = "weapons/models/"
CATALOG_VERSION = 2


@dataclass(frozen=True)
class Agent:
    key: str  # "ctm_sas/ctm_sas_variantf"
    model_path: str  # "agents/models/ctm_sas/ctm_sas_variantf.vmdl_c"
    faction: str  # "ctm_sas"

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


def agents_from_entries(entries: list[PackageEntry]) -> list[Agent]:
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
        agents.append(Agent(key=f"{parts[0]}/{name}", model_path=path, faction=parts[0]))
    return sorted(agents, key=lambda a: a.key)


@dataclass(frozen=True)
class Weapon:
    key: str  # "ak47", "knife_karambit"
    model_path: str  # "weapons/models/ak47/weapon_rif_ak47.vmdl_c"
    kind: str  # "rif", "pist", "smg", "snip", "shot", "mach", "knife", or "other"

    @property
    def label(self) -> str:
        return self.key.replace("_", " ")


_WEAPON_KINDS = ("rif", "pist", "smg", "snip", "shot", "mach", "knife")
_WEAPON_SKIP = ("_mag", "_broken_glass")


def weapons_from_entries(entries: list[PackageEntry]) -> list[Weapon]:
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
        weapons.append(Weapon(key=key, model_path=path, kind=kind))
    return sorted(weapons, key=lambda w: (w.kind, w.key))


def clips_from_entries(entries: list[PackageEntry]) -> list[str]:
    clips = []
    for entry in entries:
        path = entry.path
        if path.startswith(CLIP_PREFIX) and path.endswith(".vnmclip_c") and "+" not in path:
            clips.append(path[: -len(".vnmclip_c")])
    return sorted(clips)


def build_catalog(converter: Converter, package: Path) -> dict:
    agents = agents_from_entries(converter.list_package(package, AGENT_PREFIX, "vmdl_c"))
    clips = clips_from_entries(converter.list_package(package, CLIP_PREFIX, "vnmclip_c"))
    weapons = weapons_from_entries(converter.list_package(package, WEAPON_PREFIX, "vmdl_c"))
    return {
        "version": CATALOG_VERSION,
        "agents": [asdict(a) for a in agents],
        "weapons": [asdict(w) for w in weapons],
        "clips": clips,
    }


def load_or_build_catalog(converter: Converter, package: Path, cache_file: Path) -> dict:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("version") == CATALOG_VERSION:
            return data
    except (OSError, ValueError):
        pass
    data = build_catalog(converter, package)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(cache_file)
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
SHARED_CLIP_FAMILY = "shared"  # deaths, flinches and jumps, played over any weapon


def clip_family(weapon: Weapon) -> str:
    """'rifle', 'pistol', 'knife', 'grenade' or 'equipment': which clips hold this weapon correctly."""
    if weapon.kind in _CLIP_FAMILY:
        return _CLIP_FAMILY[weapon.kind]
    return "equipment" if weapon.key in _EQUIPMENT else "grenade"


def family_of_clip(clip: str) -> str:
    """'animation/anims/world/rifle/rifle_ak/run' or its label -> 'rifle'; '' outside the world clips."""
    parts = clip_label(clip).split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "world" else ""


def clip_fits(clip: str, weapon: Weapon | None) -> bool:
    return weapon is None or family_of_clip(clip) in (clip_family(weapon), SHARED_CLIP_FAMILY)


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