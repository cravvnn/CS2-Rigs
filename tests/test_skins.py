import json

from core import skins

ITEMS = """\
"items_game"
{
    "items"
    {
        "1" { "name" "weapon_ak47" "prefab" "rifle" }
        "2" { "name" "weapon_awp" "prefab" "sniper_rifle" }
        "3" { "name" "weapon_m4a1" "prefab" "rifle" }
        "4" { "name" "weapon_famas" "prefab" "rifle" }
        "5" { "name" "weapon_knife_karambit" "prefab" "melee_unusual" }
        "6" { "name" "studded_bloodhound_gloves" "prefab" "hands_paintable" }
    }
    "item_sets"
    {
        "set_test"
        {
            "items"
            {
                "[cu_awp_cobra]weapon_awp" "1"
                "[zz_aaa]weapon_ak47" "1"
                "[cu_m4a1_howling]weapon_m4a1" "1"
                "[cu_m4a1_howling]weapon_famas" "1"
            }
        }
    }
    "client_loot_lists"
    {
        "list_test"
        {
            "[cu_ak47_cobra]weapon_ak47" "1"
            "[bloodhound_black_silver]studded_bloodhound_gloves" "1"
        }
    }
    "paint_kits_rarity"
    {
        "cu_ak47_cobra" "mythical"
        "cu_awp_cobra" "legendary"
        "cu_m4a1_howling" "immortal"
        "bloodhound_black_silver" "ancient"
    }
    "paint_kits"
    {
        "0"
        {
            "name" "default"
        }
        "9001"
        {
            "name" "workshop_default"
        }
        "10"
        {
            "name" "cu_ak47_cobra"
            "description_tag" "#PaintKit_cu_ak47_cobra_Tag"
            "style" "7"
            "wear_remap_min" "0.100000"
            "wear_remap_max" "0.700000"
            "pattern" "661"
        }
        "11"
        {
            "name" "cu_awp_cobra"
            "description_tag" "#PaintKit_cu_awp_cobra_Tag"
            "style" "7"
        }
        "12"
        {
            "name" "cu_m4a1_howling"
            "description_tag" "#PaintKit_cu_m4a1_howling_Tag"
            "style" "7"
        }
        "13"
        {
            "name" "bloodhound_black_silver"
            "description_tag" "#PaintKit_Bloodhound_Black_Silver_Tag"
            "vmt_path" "materials/models/weapons/customization/paints_gloves/bloodhound_black_silver.vmat"
        }
        "14"
        {
            "name" "an_tiger_orange"
            "description_tag" "#PaintKit_an_tiger_orange_Tag"
            "style" "5"
        }
        "15"
        {
            "name" "zz_missing_fields"
            "vmt_path" "materials/models/weapons/customization/paints_gloves/unlisted.vmat"
            "pattern" "42"
        }
        "16"
        {
            "name" "glove_driver_unlisted"
            "description_tag" "#PaintKit_absent_Tag"
            "composite_material_path" "gloves/paints/volatile_02/driver.vcompmat"
        }
        "17"
        {
            "name" "zz_aaa"
            "description_tag" "#PaintKit_zz_aaa_Tag"
        }
    }
}
"""

ENGLISH = """\
"lang"
{
    "Language" "English"
    "Tokens"
    {
        "PaintKit_cu_ak47_cobra_Tag" "Cobra"
        "PaintKit_cu_awp_cobra_Tag" "Lightning"
        "PaintKit_cu_m4a1_howling_Tag" "Howl"
        "PaintKit_bloodhound_black_silver_tag" "Charred"
        "PaintKit_an_tiger_orange_Tag" "Tiger Tooth"
        "PaintKit_zz_aaa_Tag" "Aardvark"
    }
}
"""


def catalogue():
    return skins.paint_kits(skins.parse_keyvalues(ITEMS), skins.parse_localisation(ENGLISH))


def assert_value_error(text):
    try:
        skins.parse_keyvalues(text)
    except ValueError as exc:
        assert str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_repeated_blocks_merge_instead_of_overwriting():
    data = skins.parse_keyvalues(
        """
        "root"
        {
            "block" { "a" "1" "nested" { "x" "1" } }
            "block" { "b" "2" "nested" { "y" "2" } }
            "scalar" "first"
            "scalar" "second"
        }
        """
    )
    assert data == {
        "root": {
            "block": {"a": "1", "b": "2", "nested": {"x": "1", "y": "2"}},
            "scalar": "second",
        }
    }


def test_comments_and_escapes():
    data = skins.parse_keyvalues(
        '"root"\n'
        '{\n'
        '    // braces in a comment: { } }}\n'
        '    "path" "C:\\\\Program Files\\\\CS2" // trailing comment\n'
        '    "quote" "say \\"hi\\""\n'
        '    "escaped" "line\\nbreak"\n'
        '    "url" "http://example.com//x"\n'
        '    "braces" "{}"\n'
        '}\n'
    )
    assert data["root"]["path"] == r"C:\Program Files\CS2"
    assert data["root"]["quote"] == 'say "hi"'
    assert data["root"]["escaped"] == "line\nbreak"
    assert data["root"]["url"] == "http://example.com//x"
    assert data["root"]["braces"] == "{}"


def test_malformed_keyvalues_raise():
    assert_value_error('"root" { "key" "value"')
    assert_value_error('"root" { "key" "value }')
    assert_value_error('"root" { } }')
    assert_value_error('{ "key" "value" }')
    assert_value_error('"root" { "key" }')


def test_localisation_is_case_insensitive():
    english = skins.parse_localisation(ENGLISH)
    assert english["paintkit_cu_ak47_cobra_tag"] == "Cobra"
    assert english["paintkit_bloodhound_black_silver_tag"] == "Charred"


def test_catalogue_skips_placeholders_and_fills_fields():
    kits = catalogue()
    assert [kit.id for kit in kits] == [10, 11, 12, 13, 14, 15, 16, 17]
    assert 0 not in [kit.id for kit in kits]
    assert 9001 not in [kit.id for kit in kits]

    cobra = kits[0]
    assert (cobra.name, cobra.label, cobra.rarity) == ("cu_ak47_cobra", "Cobra", "mythical")
    assert (cobra.style, cobra.wear_min, cobra.wear_max) == (7, 0.1, 0.7)
    assert cobra.fields["pattern"] == "661"

    missing = next(kit for kit in kits if kit.id == 15)
    assert (missing.label, missing.rarity, missing.style) == ("zz_missing_fields", "", 0)
    assert (missing.wear_min, missing.wear_max) == (skins.DEFAULT_WEAR_MIN, skins.DEFAULT_WEAR_MAX)
    assert missing.fields["pattern"] == "42"


def test_kit_weapons_come_from_every_source():
    kits = {kit.name: kit for kit in catalogue()}
    # client_loot_lists
    assert kits["cu_ak47_cobra"].weapons == ("weapon_ak47",)
    # item_sets
    assert kits["cu_awp_cobra"].weapons == ("weapon_awp",)
    # one kit on two weapons
    assert kits["cu_m4a1_howling"].weapons == ("weapon_famas", "weapon_m4a1")
    # glove item names are kept as they appear in the data
    assert kits["bloodhound_black_silver"].weapons == ("studded_bloodhound_gloves",)


def test_knives_and_gloves_are_linked_through_icon_names():
    data = skins.parse_keyvalues(ITEMS)
    folder = skins.ICON_FOLDER
    icons = [
        folder + "weapon_knife_karambit_an_tiger_orange_light_png.vtex_c",
        folder + "weapon_knife_karambit_an_tiger_orange_heavy_png.vtex_c",
        folder + "studded_bloodhound_gloves_glove_driver_unlisted_light_png.vtex_c",
        folder + "weapon_ak47_not_a_kit_light_png.vtex_c",
        folder + "unknown_item_an_tiger_orange_light_png.vtex_c",
        "panorama/images/other/weapon_awp_an_tiger_orange_light_png.vtex_c",
    ]
    assert skins.icon_links(icons, data) == {
        "an_tiger_orange": {"weapon_knife_karambit"},
        "glove_driver_unlisted": {"studded_bloodhound_gloves"},
    }
    kits = {kit.name: kit for kit in skins.paint_kits(data, {}, icons)}
    assert kits["an_tiger_orange"].weapons == ("weapon_knife_karambit",)
    assert kits["cu_ak47_cobra"].weapons == ("weapon_ak47",)


def test_kits_without_a_link_stay_unmapped():
    kits = {kit.name: kit for kit in catalogue()}
    assert kits["an_tiger_orange"].weapons == ()
    assert kits["glove_driver_unlisted"].weapons == ()
    assert kits["glove_driver_unlisted"].label == "glove_driver_unlisted"

def test_kits_for_is_sorted_by_label():
    kits = catalogue()
    assert [kit.label for kit in skins.kits_for(kits, "weapon_ak47")] == ["Aardvark", "Cobra"]
    assert skins.kits_for(kits, "weapon_none") == []


def test_json_round_trip_returns_equal_kits():
    kits = catalogue()
    encoded = json.loads(json.dumps(skins.to_json(kits)))
    assert skins.from_json(encoded) == kits


def test_item_names_and_body_choice():
    kits = {kit.name: kit for kit in catalogue()}
    assert not skins.uses_legacy_model(kits["cu_ak47_cobra"])
    assert skins.item_name("ak47") == "weapon_ak47"
    assert skins.item_name("knife_bayonet") == "weapon_bayonet"
    assert skins.item_name("knife_karambit") == "weapon_knife_karambit"
