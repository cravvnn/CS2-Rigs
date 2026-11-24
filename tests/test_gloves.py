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
    older = {
        "g_tLayerMask": "m",
        "g_tSurface": "s",
        "g_tNormal": "n",
        "g_tDetail1": "d",
        "g_tGrunge1": "g",
        "g_tDetailNormal1": "dn",
        "g_tDamageNormal1": "xn",
        "F_PATTERN": 1,
        "g_tPattern": "p",
    }
    assert gloves.generation(older) == 1
    assert gloves.texture_names(older) == ["g_tLayerMask", "g_tSurface", "g_tNormal", "g_tDetail1", "g_tGrunge1", "g_tDetailNormal1", "g_tDamageNormal1", "g_tPattern"]
    assert gloves.raw_texture_names(older) == set()
    assert "g_tPattern" not in gloves.texture_names({**older, "F_PATTERN": 0})
    newer = {
        "g_tLayerId": "i",
        "g_tTintId": "t",
        "g_tSurface2": "s",
        "g_tSurfaceNormal2": "sn",
        "g_tSubstrateNormal2": "bn",
        "F_PATTERN": 1,
        "F_PATTERN_PAINT": 1,
        "g_tPattern": "p",
        "g_tPatternProperties": "pp",
    }
    assert gloves.generation(newer) == 2
    assert gloves.texture_names(newer) == ["g_tLayerId", "g_tTintId", "g_tSurface2", "g_tSurfaceNormal2", "g_tSubstrateNormal2", "g_tPattern", "g_tPatternProperties"]
    assert gloves.raw_texture_names(newer) == {"g_tSurfaceNormal2", "g_tSubstrateNormal2"}


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
    assert out["normal"].shape == (8, 8, 3) and out["occlusion"].shape == (8, 8)
    left, right = out["colour"][4, 1], out["colour"][4, 6]
    assert abs(float(left[0] - left[1])) < 0.05  # neutral grey leather (with subtle shader noise tint)
    assert right[0] > 0.9 and right[1] < 0.2 and right[0] - right[2] > 0.5  # red tint dominates over G and B
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
        "F_TINT_ID": 1,
        "F_PATTERN": 0,
        "g_vId1Color": (1.0, 1.0, 1.0, 0.0),
        "g_vId2Color": (0.0, 1.0, 0.0, 1.0),
        "g_vSurfaceColorTint1": (1.0, 1.0, 1.0, 0.0),
        "g_vDamageMinMax1": (0.0, 1.0, 0.0, 0.0),
    }
    layer_id = np.zeros((8, 8, 4), dtype=np.float32)
    layer_id[..., 0] = 1.0  # red = layer 1 in the Source2 shader
    ids = np.zeros((8, 8, 4), dtype=np.float32)
    ids[:, 4:, 0] = 32.5 / 255.0  # ceil(32.5/255 * 7) = 1 -> g_vId2Color on the right half
    textures = {"g_tLayerId": layer_id, "g_tTintId": ids, "g_tSurface1": flat((0.5, 0.5, 0.5, 1.0), 8)}
    out = gloves.compose(values, textures, 8, wear=0.0)
    assert out["colour"][4, 6][1] > 0.9 and out["colour"][4, 6][0] < 0.35 and out["colour"][4, 6][2] < 0.35
    assert abs(float(out["colour"][4, 1][0] - out["colour"][4, 1][1])) < 0.02  # untinted neutral surface on the left


def test_flip_fixup_mirrors_patterns_horizontally_in_both_generations():
    pattern = np.zeros((8, 8, 4), dtype=np.float32)
    pattern[:, :4, 0] = 1.0  # red on the left half, black on the right
    pattern[..., 3] = 1.0

    # Generation 1 (bitmap pattern, unmasked)
    v1 = older_values(F_PATTERN=1, g_nPatternMode=3, g_fPatternTexCoordScale=1.0, g_fPatternTexCoordRotation=0.0)
    t1 = {**older_textures(), "g_tPattern": pattern}
    right_1 = gloves.compose({**v1, "g_bFlipFixup": 0}, t1, 8, wear=0.0)["colour"]
    left_1 = gloves.compose({**v1, "g_bFlipFixup": 1}, t1, 8, wear=0.0)["colour"]
    assert right_1[4, 1, 0] > 0.5 and right_1[4, 6, 0] < 0.1
    assert left_1[4, 6, 0] > 0.5 and left_1[4, 1, 0] < 0.1

    # Generation 2 (paint layer pattern)
    layer_id = np.zeros((8, 8, 4), dtype=np.float32)
    layer_id[..., 0] = 1.0
    v2 = {"g_tLayerId": "i", "F_TINT_ID": 0, "F_PATTERN": 1, "F_PATTERN_PAINT": 1, "g_vPatternTexCoordScale": (1.0, 1.0)}
    t2 = {"g_tLayerId": layer_id, "g_tSurface1": flat((0.5, 0.5, 0.5, 1.0), 8), "g_tPattern": pattern}
    right_2 = gloves.compose({**v2, "g_bFlipFixup": 0}, t2, 8, wear=0.0)["colour"]
    left_2 = gloves.compose({**v2, "g_bFlipFixup": 1}, t2, 8, wear=0.0)["colour"]
    assert right_2[4, 1, 0] > 0.8 and right_2[4, 6, 0] < 0.1
    assert left_2[4, 6, 0] > 0.8 and left_2[4, 1, 0] < 0.1


def test_detail_and_damage_normals_compose_into_output_normal():
    tilted_x = flat((0.9, 0.5, 0.8, 1.0), 8)  # positive X slope
    tilted_y = flat((0.5, 0.9, 0.8, 1.0), 8)  # positive Y slope
    base_flat = flat((0.5, 0.5, 1.0, 1.0), 8)
    values = older_values(g_fDetailNormalContrast1=1.0, g_vDamageLevels1=(0.0, 0.2, 0.0, 0.0))
    textures = {
        **older_textures(),
        "g_tNormal": base_flat,
        "g_tDetailNormal1": tilted_x,
        "g_tDamageNormal1": tilted_y,
        "g_tSurface": flat((1.0, 1.0, 0.0, 1.0), 8),
        "g_tGrunge1": flat(1.0, 8),
    }
    fresh_n = gloves.compose(values, textures, 8, wear=0.0)["normal"][4, 1]
    worn_n = gloves.compose(values, textures, 8, wear=1.0)["normal"][4, 1]
    assert fresh_n[0] > 0.7 and abs(float(fresh_n[1] - 0.5)) < 0.05  # detail normal dominates when fresh
    assert worn_n[1] > 0.7 and abs(float(worn_n[0] - 0.5)) < 0.05  # damage normal takes over when worn


def test_color_adjust_matrices_match_identity_brightness_and_tint():
    ident_cc = gloves.matrix_color_correct2(1.0, 1.0, 1.0, (0.5, 0.5, 0.5))
    assert np.allclose(ident_cc, np.eye(4, dtype=np.float32), atol=1e-5)

    bright = gloves.matrix_color_correct2(1.0, 1.0, 2.0, (0.5, 0.5, 0.5))
    rgb = np.array((0.2, 0.3, 0.4, 1.0), dtype=np.float32)
    assert np.allclose((rgb @ bright)[:3], (0.4, 0.6, 0.8), atol=1e-5)

    ident_tint = gloves.matrix_color_tint2((1.0, 1.0, 1.0), 1.0)
    assert np.allclose(ident_tint, np.eye(4, dtype=np.float32), atol=1e-5)

    red_tint = gloves.matrix_color_tint2((1.0, 0.0, 0.0), 1.0)
    tinted = (np.array((0.5, 0.5, 0.5, 1.0), dtype=np.float32) @ red_tint)[:3]
    assert tinted[0] > tinted[1] and tinted[0] > tinted[2]


def test_hemioct_aniso_decoding_recovers_normal_and_roughness():
    raw = np.array([[[0.6, 0.5019608, 0.4, 0.5019608]]], dtype=np.float32)
    normal, rough = gloves._decode_hemioct_aniso(raw)
    assert np.allclose(normal[0, 0], (0.0, 0.0, 1.0), atol=1e-3)
    assert np.allclose(rough[0, 0], (0.6, 0.4), atol=1e-5)


def test_newer_shader_layer_order_and_substrate_wear():
    layer_id = np.zeros((8, 8, 4), dtype=np.float32)
    layer_id[:4, :4, 0] = 1.0  # layer 1 (R)
    layer_id[:4, 4:, 1] = 1.0  # layer 2 (G)
    layer_id[4:, :4, 2] = 1.0  # layer 3 (B)
    # bottom-right stays (0, 0, 0) -> layer 4
    values = {"g_tLayerId": "i", "F_TINT_ID": 0, "F_PATTERN": 0, "g_vDamageMinMax1": (0.0, 1.0)}
    textures = {
        "g_tLayerId": layer_id,
        "g_tSurface1": flat((1.0, 0.0, 0.0, 0.0), 8),
        "g_tSubstrate1": flat((0.0, 0.0, 1.0, 0.0), 8),
        "g_tSurface2": flat((0.0, 1.0, 0.0, 0.0), 8),
        "g_tSurface3": flat((0.0, 0.0, 1.0, 0.0), 8),
        "g_tSurface4": flat((1.0, 1.0, 0.0, 0.0), 8),
        "g_tSurfaceProperties1": flat((1.0, 0.0, 0.0, 0.8), 8),
        "g_tSubstrateProperties1": flat((1.0, 0.0, 0.0, 0.2), 8),
        "g_tDamage1": flat(1.0, 8),
        "g_tObjectProperties": flat((1.0, 0.4, 0.0, 0.0), 8),
    }
    fresh = gloves.compose(values, textures, 8, wear=0.0)["colour"]
    assert fresh[1, 1, 0] > 0.9 and fresh[1, 1, 1] < 0.05  # layer 1 red
    assert fresh[1, 6, 1] > 0.9 and fresh[1, 6, 0] < 0.05  # layer 2 green
    assert fresh[6, 1, 2] > 0.9 and fresh[6, 1, 0] < 0.05  # layer 3 blue
    assert fresh[6, 6, 0] > 0.9 and fresh[6, 6, 1] > 0.9 and fresh[6, 6, 2] < 0.05  # layer 4 yellow

    worn = gloves.compose(values, textures, 8, wear=1.0)["colour"]
    assert worn[1, 1, 2] > worn[1, 1, 0]  # worn layer 1 exposes blue substrate


def test_cloth_gets_a_sheen_only_where_the_finish_is_cloth_and_not_metal():
    values = older_values(F_CLOTH_SHADING=1, g_fDetailCloth1=1.0, g_fDetailCloth2=1.0, g_flSheenScale=2.0)
    out = gloves.compose(values, older_textures(), 8, wear=0.0)
    assert out["cloth"].shape == (8, 8) and out["sheen"].shape == (8, 8, 3)
    assert out["cloth"][4, 1] == pytest.approx(1.0)  # leather on the left is cloth
    assert out["cloth"][4, 6] == pytest.approx(0.0)  # the metal on the right never is
    off = gloves.compose({**values, "F_CLOTH_SHADING": 0}, older_textures(), 8, wear=0.0)
    assert off["cloth"].max() == 0.0


def test_the_sheen_is_the_square_root_of_the_colour_scaled_and_clamped():
    colour = np.array([[[0.25, 0.04, 1.0]]], dtype=np.float32)
    cloth, tint = gloves.sheen({"F_CLOTH_SHADING": 1}, colour, np.ones((1, 1), dtype=np.float32))
    assert tint[0, 0] == pytest.approx((0.5 * 0.667, 0.2 * 0.667, 0.667), rel=1e-4)  # the shader's default scale
    _, bright = gloves.sheen({"F_CLOTH_SHADING": 1, "g_flSheenScale": 2.5}, colour, cloth)
    assert bright[0, 0] == pytest.approx((1.0, 0.5, 1.0), rel=1e-4)


def test_the_newer_shader_reads_cloth_from_the_layer_properties():
    values = {"g_tLayerId": "i", "g_tSurfaceProperties1": "sp", "g_tSubstrateProperties1": "bp", "F_CLOTH_SHADING": 1, "g_fDamageBevelCloth1": 1.0, "g_vDamageMinMax1": (0.0, 1.0, 0.0, 0.0)}
    layer_id = np.zeros((8, 8, 4), dtype=np.float32)
    layer_id[..., 0] = 1.0
    props = flat((1.0, 0.0, 1.0, 0.8), 8)  # occlusion, metalness, cloth, height
    textures = {"g_tLayerId": layer_id, "g_tSurface1": flat((0.5, 0.5, 0.5, 1.0), 8), "g_tSurfaceProperties1": props, "g_tSubstrateProperties1": props}
    out = gloves.compose(values, textures, 8, wear=0.0)
    assert out["cloth"].min() == pytest.approx(1.0)
    metal = {**textures, "g_tSurfaceProperties1": flat((1.0, 1.0, 1.0, 0.8), 8), "g_tSubstrateProperties1": flat((1.0, 1.0, 1.0, 0.2), 8)}
    assert gloves.compose(values, metal, 8, wear=0.0)["cloth"].max() == pytest.approx(0.0)
