"""Unit tests for cs2_rigs.core.stickers (Blender-free)."""

from __future__ import annotations

import json
from pathlib import Path

from core import stickers as st


SAMPLE_ITEMS_GAME = {
    "items_game": {
        "sticker_kits": {
            "0": {"name": "default", "item_name": "#StickerKit_default"},
            "48": {
                "name": "kat2014_ibuypower_holo",
                "item_name": "#StickerKit_kat2014_ibuypower_holo",
                "sticker_material": "emskatowice2014/ibuypower_holo",
                "item_rarity": "mythical",
                "tournament_event_id": "3",
            },
            "76": {
                "name": "comm01_howling_dawn",
                "item_name": "#StickerKit_comm01_howling_dawn",
                "sticker_material": "community01/howling_dawn",
                "item_rarity": "immortal",
            },
            "97": {
                "name": "std_crown_foil",
                "item_name": "#StickerKit_std_crown_foil",
                "sticker_material": "stickers2/crown_foil",
                "item_rarity": "mythical",
            },
            "500": {
                "name": "antwerp2022_navi_glitter",
                "item_name": "#StickerKit_antwerp2022_navi_glitter",
                "sticker_material": "antwerp2022/navi_glitter",
                "item_rarity": "mythical",
                "tournament_event_id": "19",
            },
            "501": {
                "name": "antwerp2022_s1mple_gold",
                "item_name": "#StickerKit_antwerp2022_s1mple_gold",
                "sticker_material": "antwerp2022/sig_s1mple_gold",
                "item_rarity": "ancient",
                "tournament_event_id": "19",
            },
            "502": {
                "name": "lenticular_test",
                "item_name": "#StickerKit_lenticular_test",
                "sticker_material": "capsule/prism_lenticular",
                "item_rarity": "legendary",
            },
            "900": {
                "name": "spray_std_gg",
                "item_name": "#SprayKit_std_gg",
                "sticker_material": "sprays/spray_std_gg",
                "item_rarity": "common",
            },
            "901": {
                "name": "patch_phoenix",
                "item_name": "#PatchKit_phoenix",
                "patch_material": "patches/phoenix",
                "item_rarity": "rare",
            },
        }
    }
}

SAMPLE_ENGLISH = {
    "stickerkit_kat2014_ibuypower_holo": "iBUYPOWER (Holo) | Katowice 2014",
    "stickerkit_comm01_howling_dawn": "Howling Dawn",
    "stickerkit_std_crown_foil": "Crown (Foil)",
    "stickerkit_antwerp2022_navi_glitter": "Natus Vincere (Glitter) | Antwerp 2022",
    "stickerkit_antwerp2022_s1mple_gold": "s1mple (Gold) | Antwerp 2022",
    "stickerkit_lenticular_test": "Prism (Lenticular)",
    "csgo_tournament_event_name_3": "EMS One Katowice 2014",
    "csgo_tournament_event_name_19": "PGL Antwerp 2022",
}

SAMPLE_PACKAGE_PATHS = [
    "stickers/emskatowice2014/ibuypower_holo.vmat_c",
    "stickers/community01/howling_dawn.vmat_c",
    "stickers/stickers2/crown_foil.vmat_c",
    "stickers/antwerp2022/navi_glitter.vmat_c",
    "stickers/antwerp2022/sig_s1mple_gold.vmat_c",
    "stickers/capsule/prism_lenticular.vmat_c",
    "panorama/images/econ/stickers/stickers2/crown_foil_png.vtex_c",
]


def test_classify_finish_type():
    assert st.classify_finish_type("Howling Dawn", "community01/howling_dawn") == "Paper"
    assert st.classify_finish_type("Crown (Foil)", "stickers2/crown_foil") == "Foil"
    assert st.classify_finish_type("iBUYPOWER (Holo) | Katowice 2014", "emskatowice2014/ibuypower_holo") == "Holo"
    assert st.classify_finish_type("Natus Vincere (Glitter) | Antwerp 2022", "antwerp2022/navi_glitter") == "Glitter"
    assert st.classify_finish_type("s1mple (Gold) | Antwerp 2022", "antwerp2022/sig_s1mple_gold") == "Gold"
    assert st.classify_finish_type("Prism (Lenticular)", "capsule/prism_lenticular") == "Lenticular"


def test_sticker_kits_filters_patches_and_graffiti():
    kits = st.sticker_kits(SAMPLE_ITEMS_GAME, SAMPLE_ENGLISH, SAMPLE_PACKAGE_PATHS)
    names = {k.name for k in kits}
    assert names == {
        "kat2014_ibuypower_holo",
        "comm01_howling_dawn",
        "std_crown_foil",
        "antwerp2022_navi_glitter",
        "antwerp2022_s1mple_gold",
        "lenticular_test",
    }
    crown = next(k for k in kits if k.name == "std_crown_foil")
    assert crown.label == "Crown (Foil)"
    assert crown.finish_type == "Foil"
    assert crown.vmat_path == "stickers/stickers2/crown_foil.vmat"
    assert crown.icon == "panorama/images/econ/stickers/stickers2/crown_foil_png.vtex_c"

    ibp = next(k for k in kits if k.name == "kat2014_ibuypower_holo")
    assert ibp.tournament_id == 3
    assert ibp.collection == "EMS One Katowice 2014"

    # Without package_paths, heuristic graffiti filter still drops spray_std_gg
    kits_no_pkg = st.sticker_kits(SAMPLE_ITEMS_GAME, SAMPLE_ENGLISH)
    assert "spray_std_gg" not in {k.name for k in kits_no_pkg}
    assert "patch_phoenix" not in {k.name for k in kits_no_pkg}


def test_sticker_kits_json_roundtrip():
    kits = st.sticker_kits(SAMPLE_ITEMS_GAME, SAMPLE_ENGLISH, SAMPLE_PACKAGE_PATHS)
    restored = st.from_json(json.loads(json.dumps(st.to_json(kits))))
    assert restored == kits


def test_search_stickers():
    kits = st.sticker_kits(SAMPLE_ITEMS_GAME, SAMPLE_ENGLISH, SAMPLE_PACKAGE_PATHS)
    assert [k.name for k in st.search_stickers(kits, "crown foil")] == ["std_crown_foil"]
    assert [k.name for k in st.search_stickers(kits, "antwerp", finish_type="Gold")] == ["antwerp2022_s1mple_gold"]
    assert len(st.search_stickers(kits, "", collection="Antwerp 2022")) == 2
    assert len(st.search_stickers(kits, "", limit=2)) == 2


def test_parse_body_vmat_slots():
    vmat = {
        "g_vSticker0Offset": [0.154, -0.438],
        "g_vSticker0Scale": [15.35, -15.35],
        "g_flSticker0Rotation": 0.0,
        "g_vSticker1Offset": [0.065, -0.432],
        "g_vSticker1Scale": [-15.35, 15.35],
        "g_flSticker1Rotation": 0.025,
        "g_tStickerWepInputs": "weapons/models/ak47/materials/ak47_wep_inputs.vtex",
    }
    defaults = st.parse_body_vmat_slots(vmat)
    assert defaults.weapon_inputs == "weapons/models/ak47/materials/ak47_wep_inputs.vtex"
    assert defaults.slots[0].offset == (0.154, -0.438)
    assert defaults.slots[0].scale == 15.35
    assert defaults.slots[1].scale == 15.35
    assert defaults.slots[1].rotation_turns == 0.025
    assert defaults.slots[4].scale == 0.0


def test_m4a4_legacy_markup_merge_and_remap():
    vmdl_dump = '''
    m_modelInfo = {
        m_keyValueText = """
<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:vmdlkeys4:version{1234} -->
{
    StickerMarkup =
    [
        { Index = 0 Mesh = "body_hd" Offset = [ 0.096, -0.382 ] Scale = 14.05 Rotation = 0.0 SpecialIdentifier = "Autograph" }
        { Index = 1 Mesh = "body_hd" Offset = [ 0.028, -0.389 ] Scale = 14.05 Rotation = 0.0 SpecialIdentifier = "Team1" }
        { Index = 2 Mesh = "body_hd" Offset = [ -0.040, -0.354 ] Scale = 14.05 Rotation = 0.0 SpecialIdentifier = "Team2" }
        { Index = 3 Mesh = "body_hd" Offset = [ 0.287, -0.381 ] Scale = 14.05 Rotation = 0.0 SpecialIdentifier = "Map" }
        { Index = 0 Mesh = "body_legacy" LegacyModel = true Deprecated = true Offset = [ 0.091, -0.370 ] Scale = 15.0 Rotation = 0.0 }
        { Index = 1 Mesh = "body_legacy" LegacyModel = true Deprecated = true Offset = [ 0.015, -0.399 ] Scale = 15.05 Rotation = 0.0 }
        { Index = 2 Mesh = "body_legacy" LegacyModel = true Deprecated = true Offset = [ -0.027, -0.350 ] Scale = 12.55 Rotation = 0.0 }
        { Index = 3 Mesh = "body_legacy" LegacyModel = true Offset = [ 0.301, -0.394 ] Scale = 13.571 Rotation = 0.0 SpecialIdentifier = "Map" }
        { Index = 4 Mesh = "body_legacy" LegacyModel = true Offset = [ 0.0915, -0.3697 ] Scale = 13.571 Rotation = 0.0 SpecialIdentifier = "Autograph" }
        { Index = 5 Mesh = "body_legacy" LegacyModel = true Offset = [ 0.0146, -0.3986 ] Scale = 13.571 Rotation = 0.0 SpecialIdentifier = "Team1" }
        { Index = 6 Mesh = "body_legacy" LegacyModel = true Offset = [ -0.0274, -0.3500 ] Scale = 13.571 Rotation = 0.0 SpecialIdentifier = "Team2" }
    ]
}
"""
    }
    '''
    markup = st.parse_sticker_markup(vmdl_dump)
    assert len(markup["current"]) == 4
    assert len(markup["legacy"]) == 7

    legacy_vmat = st.parse_body_vmat_slots(
        {
            "g_vSticker0Offset": [0.091, -0.370],
            "g_vSticker0Scale": [15.0, 15.0],
            "g_vSticker1Offset": [0.015, -0.399],
            "g_vSticker1Scale": [15.05, 15.05],
            "g_vSticker2Offset": [-0.027, -0.350],
            "g_vSticker2Scale": [12.55, 12.55],
            "g_vSticker3Offset": [0.301, -0.394],
            "g_vSticker3Scale": [13.571, 13.571],
        }
    )
    merged_legacy = st.merge_body_slots(legacy_vmat, markup["legacy"])
    # Runtime material scales stay authoritative, while schemas come from non-deprecated markup!
    assert merged_legacy.slots[0].schema == "Autograph"
    assert merged_legacy.slots[0].scale == 15.0
    assert merged_legacy.slots[1].schema == "Team1"
    assert merged_legacy.slots[2].schema == "Team2"
    assert merged_legacy.slots[3].schema == "Map"

    hd_vmat = st.parse_body_vmat_slots(
        {
            "g_vSticker0Offset": [0.096, -0.382],
            "g_vSticker0Scale": [14.05, 14.05],
            "g_vSticker1Offset": [0.028, -0.389],
            "g_vSticker1Scale": [14.05, 14.05],
            "g_vSticker2Offset": [-0.040, -0.354],
            "g_vSticker2Scale": [14.05, 14.05],
            "g_vSticker3Offset": [0.287, -0.381],
            "g_vSticker3Scale": [16.0, 16.0],
        }
    )
    merged_hd = st.merge_body_slots(hd_vmat, markup["current"])

    # Suppose a sticker with schema="Map" was stored on slot 0 and we switch to HD where Map is slot 3
    applied = [
        st.AppliedSticker(slot=0, material="stickers2/crown_foil", schema="Map", wear=0.2),
        st.AppliedSticker(slot=3, material="community01/howling_dawn", schema="Autograph", wear=0.5),
    ]
    remapped = st.remap_stickers_for_body(applied, merged_hd)
    by_mat = {s.material: s.slot for s in remapped}
    assert by_mat["stickers2/crown_foil"] == 3
    assert by_mat["community01/howling_dawn"] == 0


def test_sticker_material_spec_and_applied_serialization(tmp_path: Path):
    spec = st.sticker_material_spec(
        {
            "g_tSticker0": "stickers/stickers2/crown_foil.vtex",
            "g_bMetallicSticker0": 1,
            "g_bPaperBackingSticker0": 0,
            "g_vColorTintSticker0": [1.0, 0.9, 0.7],
            "g_flColorBoostSticker0": 1.15,
            "g_vWearBiasSticker0": [0.8, 0.25],
        }
    )
    assert spec["albedo"] == "stickers/stickers2/crown_foil.vtex"
    assert spec["normal_roughness"] == st.DEFAULT_NORMAL_ROUGHNESS
    assert spec["is_metallic"] is True
    assert spec["is_holo"] is False
    assert spec["color_tint"] == (1.0, 0.9, 0.7)
    assert spec["wear_bias"] == (0.8, 0.25)

    applied = [
        st.AppliedSticker(slot=0, material="stickers2/crown_foil", kit_name="std_crown_foil", label="Crown (Foil)", schema="Autograph", wear=0.35),
        st.AppliedSticker(slot=4, material="community01/howling_dawn", label="Howling Dawn", wear=0.62, offset=(-0.039, -0.252), scale=15.0, rotation_deg=25.0),
    ]
    raw = st.serialize_applied(applied)
    restored = st.deserialize_applied(raw)
    assert restored == applied
    assert st.deserialize_applied("not json") == []

    # Markup cache round-trip
    sample_markup = {"current": [st.StickerSlotDefault(0, (0.1, -0.4), 14.1, 0.0, "Autograph")]}
    st.store_markup_cache(tmp_path, "weapons/models/ak47/weapon_rif_ak47.vmdl_c", sample_markup)
    assert st.read_markup_cache(tmp_path, "weapons/models/ak47/weapon_rif_ak47.vmdl_c") == sample_markup
