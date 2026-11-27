"""The sky and lights a map's entity lump carries, without Blender."""

import math

import pytest

from core import maplights

ENTITY_LUMP = r'''
[1/1] maps/de_dust2/entities/default_ents.vents_c
	Resource Type: EntityLump [Version 1] [Header Version: 12]
	File Size: 45073 bytes


--- Resource Blocks: Count 2 ---
	-- Block: RED2  Size: 1409   bytes [Offset:     48]
	-- Block: DATA  Size: 43601  bytes [Offset:   1472]


--- Data for block "RED2" ---
<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
	m_WeakReferenceList = [ ]
}


--- Data for block "DATA" ---
<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
	m_name = "default_ents"
	m_childLumps = [  ]
	m_entityKeyValues =
	[
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					brightnessscale = 1.0
					tint_color = [ 255, 255, 255 ]
					skyname = resource_name:"materials/skybox/sky_de_dust2.vmat"
					targetname = "[PR#]dust_sky"
					classname = "env_sky"
					origin = [ -2020.054321, -711.039795, 173.413345 ]
					angles = [ 0.0, 0.0, 0.0 ]
				}
				attributes = { }
			}
		},
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					brightnessscale = 1.0
					skyname = resource_name:"materials/skybox/sky_de_dust2_old.vmat"
					targetname = "[PR#]old_sky"
					classname = "env_sky"
					origin = [ 0.0, 0.0, 0.0 ]
					angles = [ 0.0, 180.0, 0.0 ]
				}
				attributes = { }
			}
		},
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					brightness = 2.5
					brightnessscale = 1.0
					color = [ 255, 222, 189 ]
					angles = [ 50.0, 43.0, 0.0 ]
					origin = [ -1733.024414, 2222.490967, 72.0 ]
					angulardiameter = 0.25
					skyintensity = 0.960784
					skytexture = "[PR#]dust_sky"
					castshadows = "1"
					classname = "light_environment"
					pathNodes = """
[
	[
		0.0, 0.0, 0.0,
	],
]
"""
				}
				attributes = { }
			}
		},
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					brightness = 0.261719
					brightnessscale = 1.0
					brightness_lumens = 300.0
					color = [ 247, 198, 130 ]
					colormode = "0"
					angles = "[ 27.992708, 132.955658, 81.811676 ]"
					origin = "[ -412.842834, 2289.02124, 146.230469 ]"
					size_params = [ 17.846497, 17.999924, 0.15 ]
					range = 816.190002
					castshadows = "2"
					classname = "light_rect"
				}
				attributes = { }
			}
		},
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					brightness_lumens = 1200.0
					brightnessscale = 1.0
					color = [ 255, 255, 255 ]
					colormode = "1"
					colortemperature = 3000.0
					angles = [ 48.832253, 238.855881, -8.400896 ]
					origin = [ 1384.908691, 967.667236, 88.0 ]
					size_params = [ 37.482834, 37.482834, 0.007808 ]
					range = 154.156998
					soft_x = 0.681
					soft_y = 0.696
					castshadows = "0"
					classname = "light_barn"
				}
				attributes = { }
			}
		},
		{
			m_connections = [  ]
			m_keyValuesData = #[  ]
			keyValues3Data =
			{
				version = 1
				values =
				{
					enabled = false
					brightness_lumens = 5000.0
					angles = [ 0.0, 0.0, 0.0 ]
					origin = [ 0.0, 0.0, 0.0 ]
					classname = "light_omni2"
				}
				attributes = { }
			}
		},
	]
}
'''

MATERIAL_DUMP = r'''
[1/1] materials/skybox/sky_de_dust2.vmat_c
	Resource Type: Material [Version 1] [Header Version: 12]
	File Size: 2076 bytes


--- Resource Blocks: Count 4 ---
	-- Block: RERL  Size: 72     bytes [Offset:     64]
	-- Block: RED2  Size: 1099   bytes [Offset:    144]
	-- Block: DATA  Size: 526    bytes [Offset:   1248]
	-- Block: INSG  Size: 300    bytes [Offset:   1776]


--- Data for block "DATA" ---
<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
	m_materialName = "materials/skybox/sky_de_dust2.vmat"
	m_shaderName = "sky.vfx"
	m_intParams = [  ]
	m_floatParams =
	[
		{
			m_name = "g_flBrightnessExposureBias"
			m_flValue = 0.765
		},
		{
			m_name = "g_flRenderOnlyExposureBias"
			m_flValue = 0.0
		},
	]
	m_vectorParams = [  ]
	m_textureParams =
	[
		{
			m_name = "g_tSkyTexture"
			m_pValue = resource:"materials/skybox/sky_de_dust2_exr_908a35ba.vtex"
		},
	]
	m_intAttributes = [  ]
	m_floatAttributes = [  ]
	m_stringAttributes = [  ]
}
'''


def entities():
    return maplights.parse_entities(ENTITY_LUMP)


def test_the_entity_lump_parses_through_a_triple_quoted_value():
    values = entities()
    assert len(values) == 6
    assert values[2]["classname"] == "light_environment"


def test_the_sun_points_at_the_env_sky_it_names():
    source = maplights.sky_source(entities())
    assert source is not None
    assert source["material"] == "materials/skybox/sky_de_dust2.vmat"
    assert source["yaw"] == 0.0
    assert source["skyintensity"] == pytest.approx(0.960784)


def test_the_sky_material_names_its_texture_and_exposure():
    found = maplights.material_sky(MATERIAL_DUMP)
    assert found == ("materials/skybox/sky_de_dust2_exr_908a35ba.vtex", pytest.approx(0.765))


def test_a_lamp_turns_lumens_into_watts_at_683_per_watt_with_the_display_gain():
    lights = maplights.lights_from_entities(entities())
    rect = next(light for light in lights if light.classname == "light_rect")
    assert rect.kind == "area"
    assert rect.energy == pytest.approx(300.0 / 683.0 * maplights.LIGHT_GAIN)
    assert rect.origin == pytest.approx((-412.842834, 2289.02124, 146.230469))
    assert rect.angles == pytest.approx((27.992708, 132.955658, 81.811676))
    assert rect.size == pytest.approx((17.846497 * 0.0254, 17.999924 * 0.0254), abs=1e-6)
    assert rect.cutoff == pytest.approx(816.190002 * 0.0254)
    assert rect.shadow is True


def test_a_barn_cone_follows_its_size_and_range_and_can_turn_shadows_off():
    lights = maplights.lights_from_entities(entities())
    barn = next(light for light in lights if light.classname == "light_barn")
    assert barn.kind == "spot"
    assert barn.energy == pytest.approx(1200.0 / 683.0 * maplights.LIGHT_GAIN)
    assert barn.spot_size == pytest.approx(math.degrees(2.0 * math.atan2(37.482834 * 0.5, 154.156998)), abs=1e-3)
    assert barn.spot_blend == pytest.approx(0.696)
    assert barn.shadow is False


def test_a_disabled_light_is_left_out():
    lights = maplights.lights_from_entities(entities())
    assert all(light.classname != "light_omni2" for light in lights)


def test_the_sun_keeps_the_gltf_exporter_brightness_and_direction():
    sun = next(light for light in maplights.lights_from_entities(entities()) if light.kind == "sun")
    assert sun.energy == pytest.approx(2.5)
    assert sun.angular_diameter == pytest.approx(0.25)
    x, y, z = maplights.blender_light_axes(sun.angles)
    # The old glTF sun's matrix for the same entity, in Blender's world axes.
    assert x == pytest.approx((-0.7313539, -0.68199825, 0.0), abs=1e-6)
    assert y == pytest.approx((0.522441, -0.5602496, 0.6427876), abs=1e-6)
    assert z == pytest.approx((-0.43837997, 0.47010514, 0.7660445), abs=1e-6)


def test_a_source_origin_becomes_blender_metres():
    assert maplights.blender_position((-1733.024414, 2222.490967, 72.0)) == pytest.approx(
        (2222.490967 * 0.0254, 1733.024414 * 0.0254, 72.0 * 0.0254)
    )


def test_the_sky_turns_a_quarter_and_follows_the_env_sky_yaw():
    assert maplights.sky_rotation(0.0) == pytest.approx(-90.0)
    assert maplights.sky_rotation(90.0) == pytest.approx(-180.0)
    assert maplights.sky_rotation(-90.0) == pytest.approx(0.0)
    source = {"material": "materials/skybox/sky.vmat", "tint": (1.0, 1.0, 1.0), "brightness": 1.0, "yaw": 0.0, "skyintensity": 0.960784}
    sky = maplights.make_sky(source, "materials/skybox/sky.vtex", "sky.exr", 0.765)
    assert sky.strength == pytest.approx(2.0**0.765 * 0.960784)
    assert sky.rotation == pytest.approx(-90.0)


def test_a_warmer_colour_temperature_is_redder_than_a_cool_one():
    warm = maplights.colour_temperature(3000.0)
    cool = maplights.colour_temperature(6600.0)
    assert warm[0] > warm[1] > warm[2]
    assert warm[2] < 0.3
    assert min(cool) > 0.9


def test_a_map_without_an_env_sky_has_no_sky():
    entities_ = [{"classname": "light_environment", "brightness": 2.0, "angles": [0.0, 0.0, 0.0]}]
    assert maplights.sky_source(entities_) is None
    assert len(maplights.lights_from_entities(entities_)) == 1
