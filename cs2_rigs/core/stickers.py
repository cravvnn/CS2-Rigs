"""Weapon stickers, slot defaults, and StickerMarkup parsing. No Blender imports here."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import finish as finish_mod
from . import proc
from . import skins as skins_mod
from .catalog import package_entries
from .converter import Converter

CATALOGUE_VERSION = 1
MARKUP_VERSION = 1
MAX_SLOTS = 5

STICKERS_PROP = "cs2rig_stickers"
STICKER_BASE_MATERIAL_PROP = "cs2rig_sticker_base_material"

FINISH_TYPES = ("Paper", "Holo", "Foil", "Glitter", "Gold", "Lenticular")

SCRATCHES_VTEX = "materials/default/stickers/sticker_default_scratches_psd_a9ad199b.vtex"
DEFAULT_HOLO_SPECTRUM = "materials/default/stickers/default_holospectrum_tga_e12c79bd.vtex"
DEFAULT_HOLO_MASK = "materials/default/stickers/default_holomask_tga_daef1ed1.vtex"
DEFAULT_NORMAL_ROUGHNESS = "materials/default/default_normal_tga_4c6e7391.vtex"

_GRAFFITI_MARKERS = ("spray_", "_graffiti")
_KEYVALUE_TEXT_RE = re.compile(r'm_keyValueText\s*=\s*"""(.*?)"""', re.S)


@dataclass(frozen=True)
class StickerKit:
    id: int
    name: str
    label: str
    material: str  # e.g. "stickers2/crown_foil"
    rarity: str
    finish_type: str  # Paper / Holo / Foil / Glitter / Gold / Lenticular
    tournament_id: int
    collection: str  # tournament name or folder/collection name
    icon: str = ""  # panorama/images/econ/stickers/<material>_png.vtex_c

    @property
    def vmat_path(self) -> str:
        return f"stickers/{self.material}.vmat"


@dataclass(frozen=True)
class StickerSlotDefault:
    index: int
    offset: tuple[float, float]
    scale: float
    rotation_turns: float
    schema: str = ""  # SpecialIdentifier: Autograph / Team1 / Team2 / Map
    deprecated: bool = False


@dataclass(frozen=True)
class BodyStickerDefaults:
    slots: dict[int, StickerSlotDefault]
    weapon_inputs: str = ""  # g_tStickerWepInputs vtex path


@dataclass(frozen=True)
class AppliedSticker:
    slot: int
    material: str
    kit_name: str = ""
    label: str = ""
    schema: str = ""
    wear: float = 0.0
    offset: tuple[float, float] | None = None
    scale: float | None = None
    rotation_deg: float | None = None


def classify_finish_type(label: str, material: str) -> str:
    """Classify a weapon sticker into Paper, Holo, Foil, Glitter, Gold, or Lenticular."""
    low_label = label.lower()
    low_mat = material.lower()
    if "(holo)" in low_label or low_mat.endswith("_holo"):
        return "Holo"
    if "(foil)" in low_label or low_mat.endswith("_foil"):
        return "Foil"
    if "(glitter)" in low_label or low_mat.endswith("_glitter"):
        return "Glitter"
    if "(gold)" in low_label or low_mat.endswith("_gold"):
        return "Gold"
    if "(lenticular)" in low_label or "lenticular" in low_mat:
        return "Lenticular"
    return "Paper"


def _is_graffiti(name: str, material: str) -> bool:
    low_name = name.lower()
    low_mat = material.lower()
    return any(m in low_name or m in low_mat for m in _GRAFFITI_MARKERS)


def _collection_name(tournament_id: int, material: str, english: dict[str, str]) -> str:
    if tournament_id > 0:
        for token in (
            f"csgo_tournament_event_name_{tournament_id}",
            f"csgo_tournament_event_nameshort_{tournament_id}",
        ):
            found = english.get(token)
            if found:
                return found
        return f"Tournament {tournament_id}"
    folder = material.split("/", 1)[0] if "/" in material else ""
    return folder.replace("_", " ").title() if folder else "General"


def sticker_kits(
    items_game: dict,
    english: dict[str, str],
    package_paths=None,
) -> list[StickerKit]:
    """Build the weapon sticker catalogue from items_game.txt, excluding graffiti and agent patches."""
    root = items_game.get("items_game")
    section = (root if isinstance(root, dict) else items_game).get("sticker_kits")
    if not isinstance(section, dict):
        return []

    entry_set: set[str] | None = None
    if package_paths is not None:
        entry_set = {p.replace("\\", "/").lower() for p in package_paths}

    kits: list[StickerKit] = []
    for raw_id, fields in section.items():
        if not isinstance(fields, dict):
            continue
        try:
            kit_id = int(raw_id)
        except ValueError:
            continue
        if fields.get("patch_material"):
            continue
        material = str(fields.get("sticker_material", "")).strip().replace("\\", "/")
        if not material:
            continue
        name = str(fields.get("name", "")).strip()
        if entry_set is not None:
            vmat_c = f"stickers/{material}.vmat_c".lower()
            if vmat_c not in entry_set:
                continue
        elif _is_graffiti(name, material):
            continue

        tag = str(fields.get("item_name", "")).strip()
        label = english.get(tag.lstrip("#").lower(), name or material) if tag else (name or material)
        rarity = str(fields.get("item_rarity", "")).strip()
        try:
            tournament_id = int(fields.get("tournament_event_id") or 0)
        except (TypeError, ValueError):
            tournament_id = 0

        icon_candidate = f"panorama/images/econ/stickers/{material}_png.vtex_c".lower()
        icon = icon_candidate if (entry_set is None or icon_candidate in entry_set) else ""

        kits.append(
            StickerKit(
                id=kit_id,
                name=name,
                label=label,
                material=material,
                rarity=rarity,
                finish_type=classify_finish_type(label, material),
                tournament_id=tournament_id,
                collection=_collection_name(tournament_id, material, english),
                icon=icon,
            )
        )
    kits.sort(key=lambda kit: (kit.label.lower(), kit.id))
    return kits


def search_stickers(
    kits: list[StickerKit],
    query: str = "",
    *,
    finish_type: str = "",
    collection: str = "",
    limit: int = 0,
) -> list[StickerKit]:
    """Fast case-insensitive multi-term substring and category search across weapon stickers."""
    terms = [t for t in query.lower().split() if t]
    ft_filter = finish_type.strip().lower()
    col_filter = collection.strip().lower()

    results: list[StickerKit] = []
    for kit in kits:
        if ft_filter and kit.finish_type.lower() != ft_filter:
            continue
        if col_filter and col_filter not in kit.collection.lower():
            continue
        if terms:
            lbl = kit.label.lower()
            nm = kit.name.lower()
            mt = kit.material.lower()
            col = kit.collection.lower()
            if not all(t in lbl or t in nm or t in mt or t in col for t in terms):
                continue
        results.append(kit)
        if limit > 0 and len(results) >= limit:
            break
    return results


def to_json(kits: list[StickerKit]) -> list[dict]:
    return [asdict(kit) for kit in kits]


def from_json(rows: list[dict]) -> list[StickerKit]:
    return [StickerKit(**row) for row in rows]


def load_or_build_catalogue(
    converter: Converter,
    package: Path,
    cache_file: Path,
    entries=None,
) -> list[StickerKit]:
    """Read weapon stickers from cache_file, or build from items_game.txt and cache."""
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if data.get("version") == CATALOGUE_VERSION:
            return from_json(data["stickers"])
    except (OSError, ValueError, KeyError, TypeError):
        pass

    with tempfile.TemporaryDirectory() as work_dir:
        items_game = skins_mod.parse_keyvalues(converter.read_text(package, skins_mod.ITEMS_GAME, Path(work_dir)))
        english = skins_mod.parse_localisation(converter.read_text(package, skins_mod.ENGLISH, Path(work_dir)))
    paths = [entry.path for entry in package_entries(converter, package, entries)]
    kits = sticker_kits(items_game, english, paths)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = proc.temp_name(cache_file)
    tmp.write_text(json.dumps({"version": CATALOGUE_VERSION, "stickers": to_json(kits)}), encoding="utf-8")
    proc.replace_file(tmp, cache_file)
    return kits


def _pair(value, default: tuple[float, float]) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return default
    return default


def parse_body_vmat_slots(vmat_values: dict) -> BodyStickerDefaults:
    """Parse runtime sticker slot defaults (0..4) and weapon inputs mask from a body .vmat_c."""
    slots: dict[int, StickerSlotDefault] = {}
    for index in range(MAX_SLOTS):
        offset = _pair(vmat_values.get(f"g_vSticker{index}Offset"), (0.0, 0.0))
        scale_raw = vmat_values.get(f"g_vSticker{index}Scale", (0.0, 0.0))
        if isinstance(scale_raw, (list, tuple)) and scale_raw:
            try:
                scale = abs(float(scale_raw[0]))
            except (TypeError, ValueError):
                scale = 0.0
        else:
            try:
                scale = abs(float(scale_raw))
            except (TypeError, ValueError):
                scale = 0.0
        try:
            rotation = float(vmat_values.get(f"g_flSticker{index}Rotation", 0.0))
        except (TypeError, ValueError):
            rotation = 0.0
        slots[index] = StickerSlotDefault(
            index=index,
            offset=offset,
            scale=scale,
            rotation_turns=rotation,
        )
    weapon_inputs = str(vmat_values.get("g_tStickerWepInputs", "") or "")
    return BodyStickerDefaults(slots=slots, weapon_inputs=weapon_inputs)


def parse_sticker_markup(vmdl_data_text: str) -> dict[str, list[StickerSlotDefault]]:
    """Extract StickerMarkup per body kind ('current' / 'legacy') from a .vmdl_c DATA dump."""
    match = _KEYVALUE_TEXT_RE.search(vmdl_data_text)
    if not match:
        return {}
    inner = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S)
    start = inner.find("{")
    if start < 0:
        return {}
    try:
        parsed = finish_mod.parse_kv3(inner[start:])
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    entries = parsed.get("StickerMarkup")
    if not isinstance(entries, list):
        return {}

    grouped: dict[str, list[StickerSlotDefault]] = {"current": [], "legacy": []}
    for item in entries:
        if not isinstance(item, dict):
            continue
        mesh = str(item.get("Mesh", "")).lower()
        is_legacy = bool(item.get("LegacyModel")) or "legacy" in mesh
        kind = "legacy" if is_legacy else "current"
        try:
            index = int(item.get("Index", 0))
        except (TypeError, ValueError):
            index = 0
        offset = _pair(item.get("Offset"), (0.0, 0.0))
        try:
            scale = abs(float(item.get("Scale", 0.0)))
        except (TypeError, ValueError):
            scale = 0.0
        try:
            rotation = float(item.get("Rotation", 0.0))
        except (TypeError, ValueError):
            rotation = 0.0
        schema = str(item.get("SpecialIdentifier", "") or "").strip()
        deprecated = bool(item.get("Deprecated", False))
        grouped[kind].append(
            StickerSlotDefault(
                index=index,
                offset=offset,
                scale=scale,
                rotation_turns=rotation,
                schema=schema,
                deprecated=deprecated,
            )
        )
    return {k: v for k, v in grouped.items() if v}


def merge_body_slots(
    vmat_defaults: BodyStickerDefaults,
    markup_slots: list[StickerSlotDefault] | None,
) -> BodyStickerDefaults:
    """Attach SpecialIdentifier schemas from StickerMarkup onto runtime .vmat slot defaults.

    Runtime offsets/scales/rotations always come from the active body .vmat_c (what the
    shader consumes). When a body has deprecated markup entries (such as M4A4 legacy, where
    slots 0..2 match deprecated entries while non-deprecated entries are 3..6), non-deprecated
    entries are matched to runtime slots by nearest offset so every slot gets its true schema.
    """
    if not markup_slots:
        return vmat_defaults

    current_entries = [m for m in markup_slots if not m.deprecated and m.schema]
    by_index = {m.index: m for m in current_entries}

    merged: dict[int, StickerSlotDefault] = {}
    used_schemas: set[str] = set()
    for idx, slot in vmat_defaults.slots.items():
        if slot.scale <= 0.0:
            merged[idx] = slot
            continue
        chosen_schema = ""
        direct = by_index.get(idx)
        if direct is not None:
            dx = direct.offset[0] - slot.offset[0]
            dy = direct.offset[1] - slot.offset[1]
            if math.hypot(dx, dy) < 0.02:
                chosen_schema = direct.schema
        if not chosen_schema and current_entries:
            best = min(
                (m for m in current_entries if m.schema not in used_schemas),
                key=lambda m: math.hypot(m.offset[0] - slot.offset[0], m.offset[1] - slot.offset[1]),
                default=None,
            )
            if best is not None and math.hypot(best.offset[0] - slot.offset[0], best.offset[1] - slot.offset[1]) < 0.03:
                chosen_schema = best.schema
        if chosen_schema:
            used_schemas.add(chosen_schema)
        merged[idx] = StickerSlotDefault(
            index=slot.index,
            offset=slot.offset,
            scale=slot.scale,
            rotation_turns=slot.rotation_turns,
            schema=chosen_schema,
            deprecated=False,
        )
    return BodyStickerDefaults(slots=merged, weapon_inputs=vmat_defaults.weapon_inputs)


def remap_stickers_for_body(
    applied: list[AppliedSticker],
    new_defaults: BodyStickerDefaults,
) -> list[AppliedSticker]:
    """Remap applied stickers when switching bodies (legacy <-> HD), preserving schema mapping."""
    schema_to_slot = {
        slot.schema: idx
        for idx, slot in new_defaults.slots.items()
        if slot.schema and slot.scale > 0.0
    }
    remapped: list[AppliedSticker] = []
    used_slots: set[int] = set()
    for item in applied:
        target_slot = item.slot
        if item.schema and item.schema in schema_to_slot:
            candidate = schema_to_slot[item.schema]
            if candidate not in used_slots:
                target_slot = candidate
        used_slots.add(target_slot)
        new_schema = item.schema or (
            new_defaults.slots[target_slot].schema if target_slot in new_defaults.slots else ""
        )
        remapped.append(
            AppliedSticker(
                slot=target_slot,
                material=item.material,
                kit_name=item.kit_name,
                label=item.label,
                schema=new_schema,
                wear=item.wear,
                offset=item.offset,
                scale=item.scale,
                rotation_deg=item.rotation_deg,
            )
        )
    remapped.sort(key=lambda s: s.slot)
    return remapped


def sticker_material_spec(vmat_values: dict) -> dict:
    """Normalize a sticker .vmat_c value dict into explicit texture paths, flags, and scalars."""
    tint = vmat_values.get("g_vColorTintSticker0", (1.0, 1.0, 1.0))
    if not isinstance(tint, (list, tuple)) or len(tint) < 3:
        tint = (1.0, 1.0, 1.0)
    wear_bias = _pair(vmat_values.get("g_vWearBiasSticker0"), (1.0, 1.0))
    return {
        "albedo": str(vmat_values.get("g_tSticker0", "")),
        "normal_roughness": str(vmat_values.get("g_tNormalRoughnessSticker0") or DEFAULT_NORMAL_ROUGHNESS),
        "sfx_mask": str(vmat_values.get("g_tSfxMaskSticker0") or DEFAULT_HOLO_MASK),
        "holo_spectrum": str(vmat_values.get("g_tHoloSpectrumSticker0") or DEFAULT_HOLO_SPECTRUM),
        "is_metallic": bool(vmat_values.get("g_bMetallicSticker0", 0)),
        "is_holo": bool(vmat_values.get("g_bHolographicSticker0", 0)),
        "is_glitter": bool(vmat_values.get("g_bGlitterSticker0", 0)),
        "paper_backing": bool(vmat_values.get("g_bPaperBackingSticker0", 0)),
        "preserve_roughness": bool(vmat_values.get("g_bPreserveRoughnessSticker0", 0)),
        "clamp_spectrum_v": bool(vmat_values.get("g_bClampSpectrumVSticker0", 1)),
        "color_tint": (float(tint[0]), float(tint[1]), float(tint[2])),
        "color_boost": float(vmat_values.get("g_flColorBoostSticker0", 1.0)),
        "wear_bias": wear_bias,
        "wear_scratches": float(vmat_values.get("g_fWearScratchesSticker0", 1.0)),
    }


def serialize_applied(applied: list[AppliedSticker]) -> str:
    rows = []
    for item in sorted(applied, key=lambda s: s.slot):
        row = {
            "slot": int(item.slot),
            "material": item.material,
            "kit_name": item.kit_name,
            "label": item.label,
            "schema": item.schema,
            "wear": round(float(item.wear), 4),
        }
        if item.offset is not None:
            row["offset"] = [round(float(item.offset[0]), 5), round(float(item.offset[1]), 5)]
        if item.scale is not None:
            row["scale"] = round(float(item.scale), 4)
        if item.rotation_deg is not None:
            row["rotation_deg"] = round(float(item.rotation_deg), 3)
        rows.append(row)
    return json.dumps(rows)


def deserialize_applied(raw: str) -> list[AppliedSticker]:
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[AppliedSticker] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("material"):
            continue
        try:
            slot = int(row["slot"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= slot < MAX_SLOTS):
            continue
        off_raw = row.get("offset")
        offset = (float(off_raw[0]), float(off_raw[1])) if isinstance(off_raw, (list, tuple)) and len(off_raw) >= 2 else None
        scale = float(row["scale"]) if row.get("scale") is not None else None
        rot = float(row["rotation_deg"]) if row.get("rotation_deg") is not None else None
        out.append(
            AppliedSticker(
                slot=slot,
                material=str(row["material"]),
                kit_name=str(row.get("kit_name", "")),
                label=str(row.get("label", "")),
                schema=str(row.get("schema", "")),
                wear=float(row.get("wear", 0.0)),
                offset=offset,
                scale=scale,
                rotation_deg=rot,
            )
        )
    out.sort(key=lambda s: s.slot)
    return out


def _markup_cache_path(folder: Path, model_path: str) -> Path:
    digest = hashlib.sha1(model_path.encode("utf-8")).hexdigest()[:16]
    return folder / f"{digest}.json"


def read_markup_cache(folder: Path, model_path: str) -> dict[str, list[StickerSlotDefault]] | None:
    try:
        data = json.loads(_markup_cache_path(folder, model_path).read_text(encoding="utf-8"))
        if data.get("version") != MARKUP_VERSION or data.get("path") != model_path:
            return None
        out: dict[str, list[StickerSlotDefault]] = {}
        for kind, rows in data.get("markup", {}).items():
            out[kind] = [
                StickerSlotDefault(
                    index=int(r["index"]),
                    offset=(float(r["offset"][0]), float(r["offset"][1])),
                    scale=float(r["scale"]),
                    rotation_turns=float(r["rotation_turns"]),
                    schema=str(r.get("schema", "")),
                    deprecated=bool(r.get("deprecated", False)),
                )
                for r in rows
            ]
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store_markup_cache(folder: Path, model_path: str, markup: dict[str, list[StickerSlotDefault]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    target = _markup_cache_path(folder, model_path)
    payload = {
        "version": MARKUP_VERSION,
        "path": model_path,
        "markup": {kind: [asdict(s) for s in slots] for kind, slots in markup.items()},
    }
    tmp = proc.temp_name(target)
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    proc.replace_file(tmp, target)
