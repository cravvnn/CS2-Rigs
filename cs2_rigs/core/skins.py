"""Weapon skins (paint kits) read from items_game.txt. No Blender imports here."""

from __future__ import annotations

import re
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .catalog import package_entries
from .converter import Converter

DEFAULT_WEAR_MIN = 0.06
DEFAULT_WEAR_MAX = 0.80
PLACEHOLDER_KITS = frozenset({0, 9001})
ICON_FOLDER = "panorama/images/econ/default_generated/"
ICON_SUFFIX = "_light_png.vtex_c"
ITEMS_GAME = "scripts/items/items_game.txt"
ENGLISH = "resource/csgo_english.txt"
CATALOGUE_VERSION = 1
# Catalogue weapon keys whose item name is not 'weapon_<key>'.
ITEM_NAMES = {
    "glock18": "weapon_glock",
    "m4a4": "weapon_m4a1",
    "knife_bayonet": "weapon_bayonet",
    "knife_m9": "weapon_knife_m9_bayonet",
    "knife_bowie": "weapon_knife_survival_bowie",
    "knife_navaja": "weapon_knife_gypsy_jackknife",
    "knife_talon": "weapon_knife_widowmaker",
}

_TOKEN = re.compile(
    r"""
    (?P<comment>//[^\n]*)
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<bare>[^\s{}"]+)
    | (?P<quote>")
    """,
    re.VERBOSE,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _unquote(token: str) -> str:
    body = token[1:-1]
    if "\\" not in body:
        return body
    parts = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body):
            i += 1
            parts.append(_ESCAPES.get(body[i], body[i]))
        else:
            parts.append(char)
        i += 1
    return "".join(parts)


def _merge(into: dict, key: str, value) -> None:
    old = into.get(key)
    if isinstance(old, dict) and isinstance(value, dict):
        _merge_into(old, value)
    else:
        into[key] = value


def _merge_into(into: dict, extra: dict) -> None:
    for key, value in extra.items():
        _merge(into, key, value)


def parse_keyvalues(text: str) -> dict:
    """Parse Valve KeyValues text into nested dicts, merging repeated keys."""
    root: dict = {}
    stack = [root]
    key = None
    for match in _TOKEN.finditer(text):
        kind = match.lastgroup
        token = match.group()
        if kind == "comment":
            continue
        if kind == "quote":
            raise ValueError(f"unterminated string at offset {match.start()}")
        if kind == "open":
            if key is None:
                raise ValueError(f"block without a key at offset {match.start()}")
            block = stack[-1].get(key)
            if not isinstance(block, dict):
                block = {}
                _merge(stack[-1], key, block)
            stack.append(block)
            key = None
        elif kind == "close":
            if key is not None:
                raise ValueError(f"key {key!r} has no value at offset {match.start()}")
            if len(stack) == 1:
                raise ValueError(f"unbalanced '}}' at offset {match.start()}")
            stack.pop()
        else:
            value = _unquote(token) if kind == "string" else token
            if key is None:
                key = value
            else:
                _merge(stack[-1], key, value)
                key = None
    if key is not None:
        raise ValueError(f"key {key!r} has no value at end of text")
    if len(stack) != 1:
        raise ValueError("unbalanced '{' at end of text")
    return root


def parse_localisation(text: str) -> dict[str, str]:
    """Map lower-cased localisation tokens to English text."""
    lang = parse_keyvalues(text).get("lang", {})
    tokens = lang.get("Tokens", {}) if isinstance(lang, dict) else {}
    return {key.lower(): value for key, value in tokens.items() if isinstance(value, str)}


@dataclass(frozen=True)
class PaintKit:
    id: int
    name: str  # "cu_ak47_cobra"
    label: str  # "Asiimov"
    rarity: str  # "mythical", empty when the kit has no rarity entry
    style: int
    wear_min: float
    wear_max: float
    weapons: tuple[str, ...]  # item class names such as "weapon_ak47"
    fields: dict  # raw paint kit block


def _items_root(data: dict) -> dict:
    inner = data.get("items_game")
    return inner if isinstance(inner, dict) else data


def _section(data: dict, key: str) -> dict:
    section = _items_root(data).get(key)
    return section if isinstance(section, dict) else {}


def _collect_loot_weapons(section: dict, found: dict[str, set[str]]) -> None:
    for key, value in section.items():
        if isinstance(value, dict):
            _collect_loot_weapons(value, found)
            continue
        # Loot lists and item sets name a paintable entry "[kit]weapon".
        if key.startswith("[") and "]" in key:
            kit, _, weapon = key[1:].partition("]")
            if kit and weapon:
                found.setdefault(kit, set()).add(weapon)


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def icon_links(paths, items_game: dict) -> dict[str, set[str]]:
    """Which items each kit is offered on, read from the game's inventory icon names.

    items_game.txt links kits to guns only. The package holds an icon for every
    real kit and item pair, knives and gloves included, named
    '<item>_<kit>_light_png.vtex_c' under ICON_FOLDER.
    """
    items = {item.get("name") for item in _section(items_game, "items").values() if isinstance(item, dict)}
    kits = {kit.get("name") for kit in _section(items_game, "paint_kits").values() if isinstance(kit, dict)}
    found: dict[str, set[str]] = {}
    for path in paths:
        if not (path.startswith(ICON_FOLDER) and path.endswith(ICON_SUFFIX)):
            continue
        stem = path[len(ICON_FOLDER):-len(ICON_SUFFIX)]
        # Item and kit names both contain underscores; only one split matches known names.
        for cut in (i for i, char in enumerate(stem) if char == "_"):
            if stem[:cut] in items and stem[cut + 1:] in kits:
                found.setdefault(stem[cut + 1:], set()).add(stem[:cut])
                break
    return found


def paint_kits(items_game: dict, english: dict[str, str], icon_paths=()) -> list[PaintKit]:
    """Build the catalogue sorted by paint kit id. `icon_paths` is the package's file listing."""
    definitions = _section(items_game, "paint_kits")
    rarities = _section(items_game, "paint_kits_rarity")
    found = icon_links(icon_paths, items_game)
    for key in ("client_loot_lists", "item_sets"):
        _collect_loot_weapons(_section(items_game, key), found)

    kits = []
    for kit_id, fields in definitions.items():
        if not isinstance(fields, dict):
            continue
        try:
            number = int(kit_id)
        except ValueError:
            continue
        if number in PLACEHOLDER_KITS:
            continue
        name = fields.get("name", "")
        tag = fields.get("description_tag", "")
        kits.append(
            PaintKit(
                id=number,
                name=name,
                label=english.get(tag.lstrip("#").lower(), name) if tag else name,
                rarity=rarities.get(name, ""),
                style=_as_int(fields.get("style"), 0),
                wear_min=_as_float(fields.get("wear_remap_min"), DEFAULT_WEAR_MIN),
                wear_max=_as_float(fields.get("wear_remap_max"), DEFAULT_WEAR_MAX),
                weapons=tuple(sorted(found.get(name, ()))),
                fields=fields,
            )
        )
    return sorted(kits, key=lambda kit: kit.id)

def item_name(weapon_key: str) -> str:
    """The item a catalogue weapon is in items_game.txt: 'ak47' -> 'weapon_ak47'."""
    return ITEM_NAMES.get(weapon_key, f"weapon_{weapon_key}")


def uses_legacy_model(kit: PaintKit) -> bool:
    """Kits made before the weapon models were redone are laid out for the old body."""
    return kit.fields.get("use_legacy_model") == "1"


def kits_for(kits: list[PaintKit], weapon: str) -> list[PaintKit]:
    """Kits offered on one item class name, sorted by display label."""
    return sorted((kit for kit in kits if weapon in kit.weapons), key=lambda kit: (kit.label, kit.id))


def to_json(kits: list[PaintKit]) -> list[dict]:
    return [asdict(kit) for kit in kits]


def from_json(data: list[dict]) -> list[PaintKit]:
    return [PaintKit(**{**row, "weapons": tuple(row.get("weapons", ()))}) for row in data]


def load_or_build_catalogue(converter: Converter, package: Path, cache_file: Path, entries=None) -> list[PaintKit]:
    """Every paint kit in the installed game, read once and then kept in `cache_file`.

    `entries` may be the package's listing, or a function that reads it, so the
    listing can be shared with the catalogue.
    """
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("version") == CATALOGUE_VERSION:
            return from_json(data["kits"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    with tempfile.TemporaryDirectory() as work_dir:
        items_game = parse_keyvalues(converter.read_text(package, ITEMS_GAME, Path(work_dir)))
        english = parse_localisation(converter.read_text(package, ENGLISH, Path(work_dir)))
    icons = [entry.path for entry in package_entries(converter, package, entries)]
    kits = paint_kits(items_game, english, icons)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": CATALOGUE_VERSION, "kits": to_json(kits)}), encoding="utf-8")
    tmp.replace(cache_file)
    return kits