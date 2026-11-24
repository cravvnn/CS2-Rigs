import numpy as np
import pytest

from core import finish, gloves, skins

SHARED = "gloves/paints/_shared_paint_generic.vcompmat"
KIT = "gloves/paints/sporty_test.vcompmat"
PAINT = "gloves/paints/sporty_test.vmat"

RECIPE_TEXT = """
{
    m_Points =
    [
        {
            m_vecCompositeMaterialAssemblyProcedures =
            [
                {
                    m_vecCompositeInputContainers =
                    [
                        { m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_VARIABLE_FROM_TARGET_ATTR" m_strAlias = "handedness" m_strAttrNameForVar = "left" },
                        {
                            m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_LOOSE_VARIABLES"
                            m_strAlias = "paint"
                            m_vecLooseVariables =
                            [
                                { m_strName = "m_strResourceMaterial" m_nVariableType = "LOOSE_VARIABLE_TYPE_RESOURCE_MATERIAL" m_strResourceMaterial = resource_name:"items/paint.vmat" },
                            ]
                        },
                    ]
                },
            ]
        },
    ]
}
"""


def library(extra_steps=()):
    shared = {
        "includes": [],
        "containers": {
            "target": {"source": "TARGET_MATERIAL"},
            "econ_instance": {
                "source": "LOOSE_VARIABLES",
                "variables": {"g_nRandomSeed": [0, None, None], "g_nRandomSeedAlt": [0, None, None], "g_flWearAmount": [0.0, 0.0, 1.0]},
            },
            "handedness": {"source": "VARIABLE_FROM_TARGET_ATTR", "attribute": "left"},
            "material_seed": {"source": "VARIABLE_FROM_TARGET_ATTR", "attribute": "seed_offset"},
            "instance_params": {
                "source": "LOOSE_VARIABLES",
                "variables": {"g_fPatternTexCoordOffset": [[0.0, 0.0], [0.0, 0.0], [1.0, 1.0]], "g_fPatternTexCoordRotation": [0.0, 0.0, 360.0]},
            },
        },
        "steps": [
            {"do": "RANDOM_ROLL_INPUT_VARIABLES", "seed": "econ_instance.g_nRandomSeed", "roll": ["instance_params.g_fPatternTexCoordOffset.xy"], "if": [{"test": "CONTAINER_VALUE_EQUALS", "in": "material_seed", "name": "seed_offset", "value": "1", "pass": False}]},
            {"do": "RANDOM_ROLL_INPUT_VARIABLES", "seed": "econ_instance.g_nRandomSeedAlt", "roll": ["instance_params.g_fPatternTexCoordOffset.xy"], "if": [{"test": "CONTAINER_VALUE_EQUALS", "in": "material_seed", "name": "seed_offset", "value": "1", "pass": True}]},
            {
                "do": "GENERATE_TEXTURE",
                "to": "g_tColor",
                "from": "paint",
                "steps": [
                    {"do": "SET_VALUE", "to": "F_OUTPUT_MODE", "value": 0},
                    {"do": "COPY_PROPERTY", "from": "econ_instance", "name": "g_flWearAmount", "to": "g_fWearProgress"},
                    {"do": "COPY_MATCHING_KEYS", "from": "instance_params"},
                    {"do": "SET_VALUE", "to": "g_bFlipFixup", "value": 1, "if": [{"test": "CONTAINER_VALUE_EQUALS", "in": "handedness", "name": "left", "value": "1", "pass": True}]},
                ],
            },
            {"do": "GENERATE_TEXTURE", "to": "g_tMetalness", "from": "paint", "steps": [{"do": "SET_VALUE", "to": "F_OUTPUT_MODE", "value": 2}]},
            *extra_steps,
        ],
    }
    kit = {"includes": [SHARED], "containers": {"paint": {"source": "SPECIFIC_MATERIAL", "material": PAINT}}, "steps": []}
    return {
        "materials": {PAINT: {"g_tNormal": "gloves/normal.vtex", "g_vColorTint1": (0.5, 0.5, 0.5, 0.0), "F_CLOTH_SHADING": 1}},
        "recipes": {SHARED: shared, KIT: kit},
    }


def test_recipe_keeps_the_attribute_a_container_reads_and_the_material_a_variable_names():
    out = finish.recipe(finish.parse_kv3(RECIPE_TEXT))
    assert out["containers"]["handedness"] == {"source": "VARIABLE_FROM_TARGET_ATTR", "attribute": "left"}
    assert out["containers"]["paint"]["variables"]["m_strResourceMaterial"][0] == "items/paint.vmat"


def test_glove_values_carry_the_paint_and_keep_its_normal_map():
    values = finish.resolve(library(), KIT, {}, {}, seed=3, wear=0.4, attributes=gloves.attributes(left=False))
    assert values["g_tNormal"] == "gloves/normal.vtex"
    assert values["g_fWearProgress"] == pytest.approx(0.4)
    assert values["F_OUTPUT_MODE"] == 0  # the colour pass, not the metalness one that is built after it
    assert "g_bFlipFixup" not in values  # only the left glove is flipped


def test_the_left_glove_is_flipped_and_the_hands_roll_their_own_pattern_places():
    left = finish.resolve(library(), KIT, {}, {}, seed=3, attributes=gloves.attributes(left=True))
    right = finish.resolve(library(), KIT, {}, {}, seed=3, attributes=gloves.attributes(left=False))
    again = finish.resolve(library(), KIT, {}, {}, seed=3, attributes=gloves.attributes(left=False))
    assert left["g_bFlipFixup"] == 1
    assert right["g_fPatternTexCoordOffset"] == again["g_fPatternTexCoordOffset"]
    assert left["g_fPatternTexCoordOffset"] != right["g_fPatternTexCoordOffset"]
    other = finish.resolve(library(), KIT, {}, {}, seed=4, attributes=gloves.attributes(left=False))
    assert other["g_fPatternTexCoordOffset"] != right["g_fPatternTexCoordOffset"]


def test_weapon_values_still_drop_the_normal_map_a_finish_does_not_replace():
    values = finish.resolve(library(), KIT, {}, {}, seed=3)
    assert "g_tNormal" not in values


def test_a_variable_that_names_a_material_stands_for_its_values():
    lib = library()
    lib["recipes"][KIT]["containers"]["paint"] = {"source": "LOOSE_VARIABLES", "variables": {"m_strResourceMaterial": ["items/paint.vmat_c", None, None]}}
    lib["materials"]["items/paint.vmat"] = {"g_vId1Color": (1.0, 0.0, 0.0, 1.0), "g_tLayerId": "id.vtex"}
    values = finish.resolve(lib, KIT, {}, {}, seed=1, attributes=gloves.attributes(left=True))
    assert values["g_vId1Color"] == (1.0, 0.0, 0.0, 1.0)
    assert gloves.generation(values) == 2


def make_kit(name, item="sporty_gloves", **fields):
    return skins.PaintKit(id=1, name=name, label="Vice", rarity="", style=0, wear_min=0.06, wear_max=0.8, weapons=(item,), fields=fields)


def test_glove_finishes_are_listed_by_glove_and_named_with_it():
    kits = [make_kit("b", "slick_gloves"), make_kit("a"), skins.PaintKit(2, "ak", "Redline", "", 0, 0.0, 1.0, ("weapon_ak47",), {})]
    found = gloves.glove_kits(kits)
    assert [kit.name for kit in found] == ["b", "a"]  # Driver Gloves before Sport Gloves
    assert gloves.label(found[1]) == "Sport Gloves | Vice"
    assert gloves.model_path(found[1]).endswith("glove_sporty/glove_sporty.vmdl_c")
    assert not gloves.is_glove_kit(kits[2])


def test_every_glove_item_has_a_model_in_the_shared_arms_folder():
    for item, (name, path) in gloves.GLOVES.items():
        assert name and path.startswith("agents/models/shared/arms/glove_") and path.endswith(".vmdl_c"), item


def test_the_recipe_is_the_named_one_or_the_one_next_to_the_paint():
    recipes = {KIT: {}, "gloves/paints/volatile_02/n.vcompmat": {}}
    assert gloves.recipe_for(make_kit("sporty_test"), recipes) == KIT
    assert gloves.recipe_for(make_kit("absent"), recipes) is None
    assert gloves.recipe_for(make_kit("n", composite_material_path="gloves/paints/volatile_02/n.vcompmat"), recipes) == "gloves/paints/volatile_02/n.vcompmat"
    assert gloves.recipe_for(make_kit("n", composite_material_path="gloves/paints/volatile_02/gone.vcompmat"), recipes) is None


def test_hands_are_told_apart_by_their_material_file_names():
    assert gloves.is_left("characters/models/shared/arms/glove_sporty/materials/glove_sporty_left.vmat")
    assert not gloves.is_left("characters/models/shared/arms/glove_sporty/materials/glove_sporty_right.vmat")
    assert gloves.attributes(True) == {"left": 1}
    assert gloves.attributes(False) == {"seed_offset": 1}


def test_texture_names_follow_the_shader_a_finish_is_for():
    older = {"g_tLayerMask": "m", "g_tSurface": "s", "g_tDetail1": "d", "g_tGrunge1": "g", "F_PATTERN": 1, "g_tPattern": "p"}
    assert gloves.generation(older) == 1
    assert gloves.texture_names(older) == ["g_tLayerMask", "g_tSurface", "g_tDetail1", "g_tGrunge1", "g_tPattern"]
    assert "g_tPattern" not in gloves.texture_names({**older, "F_PATTERN": 0})
    newer = {"g_tLayerId": "i", "g_tTintId": "t", "g_tSurface2": "s", "g_bId2Pattern": 1, "g_tPattern": "p"}
    assert gloves.generation(newer) == 2
    assert gloves.texture_names(newer) == ["g_tLayerId", "g_tTintId", "g_tSurface2", "g_tPattern"]


def flat(value, size=4, channels=4):
    return np.full((size, size, channels), value, dtype=np.float32)


def older_values(**extra):
    values = {
        "g_vColorTint1": (0.5, 0.5, 0.5, 0.0),
        "g_vColorTint2": (1.0, 0.0, 0.0, 0.0),
        "g_fDetailMetalness1": 0.0,
        "g_fDetailMetalness2": 1.0,
        "g_fDetailRoughnessBrightness1": 1.0,
        "g_fDetailRoughnessBrightness2": 0.5,
        "g_vDamageLevels1": (0.0, 0.5, 0.0, 0.0),
        "g_fDamageBrightness1": -0.4,
        "g_fWearExponent": 1.0,
    }
    return {**values, **extra}


def older_textures():
    mask = np.zeros((8, 8, 4), dtype=np.float32)
    mask[:, 4:, 0] = 1.0  # the right half is the second material
    mask[..., 3] = 18.0 / 255.0  # tint 1 everywhere...
    mask[:, 4:, 3] = (18.0 + 29.7) / 255.0  # ...and tint 2 on the second material
    return {"g_tLayerMask": mask, "g_tSurface": flat(0.5, 8), "g_tDetail1": flat(0.8, 8), "g_tDetail2": flat(1.0, 8)}


def test_each_material_gets_its_own_tint_roughness_and_metalness():
    out = gloves.compose(older_values(), older_textures(), 8, wear=0.0)
    assert out["colour"].shape == (8, 8, 3) and out["roughness"].shape == (8, 8)
    left, right = out["colour"][4, 1], out["colour"][4, 6]
    assert left[0] == pytest.approx(left[1], abs=1e-3)  # grey leather
    assert right[0] > 0.9 and right[1] < 0.05  # the red tint
    assert out["metalness"][4, 1] == 0.0 and out["metalness"][4, 6] == 1.0
    assert out["roughness"][4, 6] < out["roughness"][4, 1]


def test_wear_darkens_a_material_that_wears_dark():
    fresh = gloves.compose(older_values(), older_textures(), 8, wear=0.0)
    worn = gloves.compose(older_values(), older_textures(), 8, wear=1.0)
    assert worn["colour"][4, 1].mean() < fresh["colour"][4, 1].mean()
    assert fresh["colour"].min() >= 0.0 and worn["colour"].max() <= 1.0


def test_a_pattern_replaces_the_tint_it_is_set_for():
    values = older_values(F_PATTERN=1, g_nPatternReplaceIndex=2, g_nPatternMode=2, g_fPatternTexCoordScale=1.0)
    textures = {**older_textures(), "g_tPattern": flat(0.0, 4, 3)}
    out = gloves.compose(values, textures, 8, wear=0.0)
    assert out["colour"][4, 6].max() < 0.05  # black pattern where the red tint was
    assert out["colour"][4, 1].mean() > 0.3  # the rest is untouched


def test_the_newer_shader_lays_tint_colours_over_the_surface():
    values = {
        "g_tLayerId": "i",
        "g_tTintId": "t",
        "g_vId1Color": (0.0, 1.0, 0.0, 1.0),
        "g_vSurfaceColorTint1": (1.0, 1.0, 1.0, 0.0),
        "g_vDamageMinMax1": (0.0, 1.0, 0.0, 0.0),
    }
    ids = np.zeros((8, 8, 4), dtype=np.float32)
    ids[:, 4:, 0] = 32.5 / 255.0  # id 1 on the right half
    textures = {"g_tLayerId": flat(0.0, 8), "g_tTintId": ids, "g_tSurface1": flat(0.5, 8)}
    out = gloves.compose(values, textures, 8, wear=0.0)
    assert out["colour"][4, 6][1] > 0.9 and out["colour"][4, 6][0] < 0.05
    assert out["colour"][4, 1].min() > 0.3  # no id: the plain surface
