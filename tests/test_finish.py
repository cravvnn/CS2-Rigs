import pytest

from core import finish

PAINT = "paints/vmats/hy_test.vmat"
SHARED = "weapons/paints/legacy/_shared.vcompmat"
KIT = "weapons/paints/legacy/hy_test.vcompmat"

SHARED_TEXT = """
{
    m_Points =
    [
        {
            m_vecCompositeMaterialAssemblyProcedures =
            [
                {
                    m_vecCompositeInputContainers =
                    [
                        { m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_TARGET_MATERIAL" m_strAlias = "target" },
                        { m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_MATERIAL_FROM_TARGET_ATTR" m_strAttrName = "composite_inputs" m_strAlias = "composite_inputs" },
                        { m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_SPECIFIC_MATERIAL" m_strSpecificContainerMaterial = resource_name:"paints/vmats/placeholder.vmat" m_strAlias = "paint" },
                        {
                            m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_LOOSE_VARIABLES"
                            m_strAlias = "econ_instance"
                            m_vecLooseVariables =
                            [
                                { m_strName = "g_nRandomSeed" m_nVariableType = "LOOSE_VARIABLE_TYPE_INTEGER1" m_nValueIntX = 0 },
                                { m_strName = "g_flWearAmount" m_nVariableType = "LOOSE_VARIABLE_TYPE_FLOAT1" m_bHasFloatBounds = true m_flValueFloatX = 0.0 m_flValueFloatX_Min = 0.0 m_flValueFloatX_Max = 1.0 },
                            ]
                        },
                        {
                            m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_LOOSE_VARIABLES"
                            m_strAlias = "grunge_wear"
                            m_vecLooseVariables =
                            [
                                { m_strName = "g_flWearTexCoordScale" m_nVariableType = "LOOSE_VARIABLE_TYPE_FLOAT1" m_flValueFloatX = 1.6 m_flValueFloatX_Min = 1.6 m_flValueFloatX_Max = 1.8 },
                            ]
                        },
                    ]
                    m_vecPropertyMutators =
                    [
                        {
                            m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_CONDITIONAL_MUTATORS"
                            m_vecConditionalMutators =
                            [
                                {
                                    m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_RANDOM_ROLL_INPUT_VARIABLES"
                                    m_strRandomRollInputVars_SeedInputVar = "econ_instance.g_nRandomSeed"
                                    m_vecRandomRollInputVars_InputVarsToRoll = [ "econ_instance.g_vPatternTexCoordOffset.xy", "grunge_wear.g_flWearTexCoordScale.x", "econ_instance.g_flAbsent.x", ]
                                },
                            ]
                        },
                        {
                            m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_GENERATE_TEXTURE"
                            m_strGenerateTexture_TargetParam = "g_tColor"
                            m_strGenerateTexture_InitialContainer = "paint"
                            m_vecTexGenInstructions =
                            [
                                { m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_COPY_MATCHING_KEYS" m_strCopyMatchingKeys_InputContainerSrc = "composite_inputs" },
                                { m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_COPY_MATCHING_KEYS" m_strCopyMatchingKeys_InputContainerSrc = "econ_instance" },
                                { m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_COPY_MATCHING_KEYS" m_strCopyMatchingKeys_InputContainerSrc = "grunge_wear" },
                                {
                                    m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_COPY_PROPERTY"
                                    m_strCopyProperty_InputContainerSrc = "paint"
                                    m_strCopyProperty_InputContainerProperty = "g_tMasks"
                                    m_strCopyProperty_TargetProperty = "g_tMasks"
                                    m_vecConditions =
                                    [
                                        {
                                            m_nMutatorCondition = "COMP_MAT_MUTATOR_CONDITION_INPUT_CONTAINER_VALUE_EQUALS"
                                            m_strMutatorConditionContainerName = "override_vars"
                                            m_strMutatorConditionContainerVarName = "g_bOverrideMasks"
                                            m_strMutatorConditionContainerVarValue = "true"
                                        },
                                    ]
                                },
                            ]
                        },
                        {
                            m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_GENERATE_TEXTURE"
                            m_strGenerateTexture_TargetParam = "g_tMetalness"
                            m_strGenerateTexture_InitialContainer = "paint"
                            m_vecTexGenInstructions =
                            [
                                {
                                    m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_SET_VALUE"
                                    m_nSetValue_Value = { m_strName = "F_ROUGHNESS_MODE" m_nVariableType = "LOOSE_VARIABLE_TYPE_BOOLEAN" m_bValueBoolean = true }
                                },
                                {
                                    m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_SET_VALUE"
                                    m_nSetValue_Value = { m_strName = "F_ROUGHNESS_TEXTURE" m_nVariableType = "LOOSE_VARIABLE_TYPE_INTEGER1" m_nValueIntX = 0 }
                                    m_vecConditions =
                                    [
                                        {
                                            m_nMutatorCondition = "COMP_MAT_MUTATOR_CONDITION_INPUT_CONTAINER_VALUE_EQUALS"
                                            m_strMutatorConditionContainerName = "override_vars"
                                            m_strMutatorConditionContainerVarName = "g_bOverrideMasks"
                                            m_strMutatorConditionContainerVarValue = "1"
                                            m_bPassWhenTrue = false
                                        },
                                    ]
                                },
                            ]
                        },
                        {
                            m_nMutatorCommandType = "COMP_MAT_PROPERTY_MUTATOR_COPY_PROPERTY"
                            m_strCopyProperty_InputContainerSrc = "override_vars"
                            m_strCopyProperty_InputContainerProperty = "normal"
                            m_strCopyProperty_TargetProperty = "g_tNormal"
                        },
                    ]
                },
            ]
        },
    ]
}
"""


def kit_text(override: str = "") -> str:
    return (
        """
{
    m_Points =
    [
        {
            m_vecCompositeMaterialAssemblyProcedures =
            [
                {
                    m_vecCompMatIncludes = [ resource_name:"%s", ]
                    m_vecCompositeInputContainers =
                    [
                        {
                            m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_LOOSE_VARIABLES"
                            m_strAlias = "econ_instance"
                            m_vecLooseVariables =
                            [
                                {
                                    m_strName = "g_vPatternTexCoordOffset"
                                    m_nVariableType = "LOOSE_VARIABLE_TYPE_FLOAT2"
                                    m_flValueFloatX = 0
                                    m_flValueFloatX_Min = 0.25
                                    m_flValueFloatX_Max = 0.5
                                    m_flValueFloatY = 0
                                    m_flValueFloatY_Min = -1
                                    m_flValueFloatY_Max = 1
                                },
                            ]
                        },
                        { m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_SPECIFIC_MATERIAL" m_strSpecificContainerMaterial = "%s" m_strAlias = "paint" },
                        %s
                    ]
                },
            ]
        },
    ]
}
"""
        % (SHARED, PAINT, override)
    )


OVERRIDE = """
{
    m_nCompositeMaterialInputContainerSourceType = "CONTAINER_SOURCE_TYPE_LOOSE_VARIABLES"
    m_strAlias = "override_vars"
    m_vecLooseVariables =
    [
        { m_strName = "g_bOverrideMasks" m_nVariableType = "LOOSE_VARIABLE_TYPE_BOOLEAN" m_bValueBoolean = true },
        { m_strName = "normal" m_nVariableType = "LOOSE_VARIABLE_TYPE_RESOURCE_TEXTURE" m_strTextureRuntimeResourcePath = resource_name:"kit/normal.vtex" },
        { m_strName = "tint" m_nVariableType = "LOOSE_VARIABLE_TYPE_COLOR4" m_cValueColor4 = [ 255, 0, 51 ] },
    ]
},
"""

PAINT_VALUES = {
    "F_PAINT_STYLE": 1,
    "F_ROUGHNESS_TEXTURE": 1,
    "g_flUvScale1": 1.0,
    "g_tMasks": "kit/masks.vtex",
    "g_tColor": "default/colour.vtex",
    "g_tPattern": "kit/pattern.vtex",
    "g_tNormal": "kit/unused_normal.vtex",
    "g_vPatternTexCoordOffset": (0.0, 0.0, 0.0, 0.0),
}
WEAPON_INPUTS = {"g_flUvScale1": 0.55, "g_tMasks": "weapon/masks.vtex", "g_tColor": "weapon/colour.vtex", "g_tPosition": "weapon/pos.vtex", "F_SEPARATE_CHANNEL_INPUTS": 1}


def library(override: str = "") -> dict:
    return {
        "materials": {PAINT: PAINT_VALUES},
        "recipes": {SHARED: finish.recipe(finish.parse_kv3(SHARED_TEXT)), KIT: finish.recipe(finish.parse_kv3(kit_text(override)))},
    }


def test_kv3_values():
    data = finish.parse_kv3(
        """
        {
            name = "a \\"quoted\\" name"
            path = resource_name:"some/file.vmat"
            on = true
            off = false
            nothing = null
            count = -3
            ratio = 0.25
            word = some_enum_value
            list = [ 1.0, 2.0, 3.0 ]
            trailing = [ "x", "y", ]
            nested = { inner = [ { k = 1 }, { k = 2 } ] }
            blob = #[ 1D 9D A9 ]
            after = 7
        }
        """
    )
    assert data["name"] == 'a "quoted" name'
    assert data["path"] == "some/file.vmat"
    assert (data["on"], data["off"], data["nothing"]) == (True, False, None)
    assert (data["count"], data["ratio"], data["word"]) == (-3, 0.25, "some_enum_value")
    assert data["list"] == [1.0, 2.0, 3.0] and data["trailing"] == ["x", "y"]
    assert [item["k"] for item in data["nested"]["inner"]] == [1, 2]
    assert data["blob"] is None and data["after"] == 7


def test_kv3_rejects_broken_text():
    for text in ("{ a = [ 1, 2 ", "{ a 1 }", "{ a = }"):
        with pytest.raises(ValueError):
            finish.parse_kv3(text)


def test_dump_of_many_files_is_split_by_path():
    dump = """
[1/2] a/first.vmat_c
\tResource Type: Material
--- Data for block "DATA" ---
<!-- kv3 encoding:text:version{e21c7f3c-8a33} format:generic:version{7412167c} -->
{
\tm_shaderName = "csgo_customweapon.vfx"
\tm_intParams = [ { m_name = "F_PAINT_STYLE" m_nValue = 6 }, ]
\tm_floatParams = [ { m_name = "g_flPaintRoughness" m_flValue = 0.4 }, ]
\tm_vectorParams = [ { m_name = "g_vColor0" m_value = [ 0.1, 0.2, 0.3, 1.0 ] }, ]
\tm_textureParams = [ { m_name = "g_tPattern" m_pValue = resource:"x/pattern.vtex" }, ]
\tm_stringAttributes = [ { m_name = "composite_inputs" m_value = "x/inputs.vmat" }, ]
}

--- Data for block "INSG" ---
{ ignored = true }
[2/2] a/broken.vmat_c
--- Data for block "DATA" ---
{ unfinished = [
"""
    files = finish.split_dump(dump)
    assert list(files) == ["a/first.vmat"]
    assert finish.material_values(files["a/first.vmat"]) == {
        "F_PAINT_STYLE": 6,
        "g_flPaintRoughness": 0.4,
        "g_vColor0": (0.1, 0.2, 0.3, 1.0),
        "g_tPattern": "x/pattern.vtex",
        "composite_inputs": "x/inputs.vmat",
    }


def test_weapon_values_replace_the_paint_materials_placeholders():
    values = finish.resolve(library(), KIT, WEAPON_INPUTS, {}, seed=5, wear=0.3)
    assert values["F_PAINT_STYLE"] == 1 and values["g_tPattern"] == "kit/pattern.vtex"
    assert values["g_tMasks"] == "weapon/masks.vtex" and values["g_tColor"] == "weapon/colour.vtex"
    assert values["g_flUvScale1"] == 0.55
    # Inputs the paint material does not mention still reach the shader.
    assert values["g_tPosition"] == "weapon/pos.vtex" and values["F_SEPARATE_CHANNEL_INPUTS"] == 1
    assert values["g_flWearAmount"] == 0.3 and values["g_nRandomSeed"] == 5
    assert "g_tNormal" not in values


def test_seed_places_the_pattern_inside_its_range_and_repeats():
    first = finish.resolve(library(), KIT, WEAPON_INPUTS, {}, seed=5)
    again = finish.resolve(library(), KIT, WEAPON_INPUTS, {}, seed=5)
    other = finish.resolve(library(), KIT, WEAPON_INPUTS, {}, seed=6)
    x, y = first["g_vPatternTexCoordOffset"]
    assert 0.25 <= x <= 0.5 and -1.0 <= y <= 1.0
    assert 1.6 <= first["g_flWearTexCoordScale"] <= 1.8
    assert first == again
    assert first["g_vPatternTexCoordOffset"] != other["g_vPatternTexCoordOffset"]


def test_conditions_and_the_surface_recipes_switches():
    plain = finish.resolve(library(), KIT, WEAPON_INPUTS, {})
    # The surface recipe turns the roughness texture off unless the kit overrides masks.
    assert plain["F_ROUGHNESS_TEXTURE"] == 0 and "F_ROUGHNESS_MODE" not in plain
    overriding = finish.resolve(library(OVERRIDE), KIT, WEAPON_INPUTS, {})
    assert overriding["g_tMasks"] == "kit/masks.vtex"
    assert overriding["F_ROUGHNESS_TEXTURE"] == 1
    assert overriding["g_tNormal"] == "kit/normal.vtex"


def test_loose_variable_kinds():
    kit = finish.recipe(finish.parse_kv3(kit_text(OVERRIDE)))
    variables = kit["containers"]["override_vars"]["variables"]
    assert variables["g_bOverrideMasks"][0] == 1
    assert variables["normal"][0] == "kit/normal.vtex"
    assert variables["tint"][0] == (1.0, 0.0, 0.2)
    assert kit["containers"]["econ_instance"]["variables"]["g_vPatternTexCoordOffset"] == [(0.0, 0.0), (0.25, -1.0), (0.5, 1.0)]
    assert kit["includes"] == [SHARED]


def test_missing_recipe_and_unshown_parts():
    with pytest.raises(KeyError):
        finish.resolve(library(), "weapons/paints/legacy/absent.vcompmat", WEAPON_INPUTS, {})
    assert finish.recipe_path("hy_test") == KIT
    assert finish.recipe_path("new_kit", "weapons/paints/set/new_kit.vcompmat") == "weapons/paints/set/new_kit.vcompmat"
    assert finish.not_shown({"F_PAINT_STYLE": 6}) == []
    assert finish.not_shown({"F_OVERLAY_TEXTURE": 1, "F_CASE_HARDENING": 1, "F_PATINA_AGE": 0}) == ["overlay", "case hardening"]
