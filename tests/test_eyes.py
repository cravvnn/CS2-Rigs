"""Unit tests for pure CS2 eyeball maths and parameter parsing."""

from __future__ import annotations

import math

import pytest

from core import eyes


def _mat3_vec(mat, vec):
    return tuple(sum(mat[r][c] * vec[c] for c in range(3)) for r in range(3))


def _mat4_det3(mat):
    a, b, c = mat[0][0], mat[0][1], mat[0][2]
    d, e, f = mat[1][0], mat[1][1], mat[1][2]
    g, h, i = mat[2][0], mat[2][1], mat[2][2]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def test_flatten_and_extract_character_eyeballs():
    vmat = {
        "Name": "characters/models/ctm_fbi/materials/ctm_fbi_v2_head_variantf.vmat",
        "ShaderName": "csgo_character.vfx",
        "IntParams": {"F_EYEBALLS": 1, "F_CLOTH_SHADING": 1},
        "FloatParams": {
            "g_flEyeBallRadius1": 0.6,
            "g_flEyeIrisSize1": 1.2,
            "g_flEyePupilSize1": 0.15,
            "g_flEyeHueShift1": 1.382,
            "g_flEyeSaturation1": 0.34,
            "g_flEyeBallWalleyeL1": 0.0,
            "g_flEyeBallWalleyeR1": 2.547,
        },
        "TextureParams": {
            "g_tEyeAlbedo1": "characters\\models\\shared\\materials\\eyes\\eyeball_brown_01_color.vtex_c",
            "g_tEyeMask1": "characters/models/ctm_fbi/materials/ctm_fbi_v2_head_variantf_eyemask.vtex",
        },
    }
    path, shader, flat = eyes.flatten_vmat_prop(vmat)
    assert path == "characters/models/ctm_fbi/materials/ctm_fbi_v2_head_variantf.vmat"
    assert shader == "csgo_character.vfx"
    params = eyes.extract_eye_params(shader, flat)
    assert params is not None
    assert params.albedo_texture == "characters/models/shared/materials/eyes/eyeball_brown_01_color.vtex"
    assert params.mask_texture == "characters/models/ctm_fbi/materials/ctm_fbi_v2_head_variantf_eyemask.vtex"
    assert params.radius == pytest.approx(0.6 * 0.0254)
    assert params.iris_size == pytest.approx(1.2)
    # Shader evaluates Expression(this * g_flEyeIrisSize1) for pupil size.
    assert params.pupil_size == pytest.approx(0.15 * 1.2)
    assert params.hue_shift == pytest.approx(1.382)
    assert params.saturation == pytest.approx(0.34)
    assert params.walleye_r == pytest.approx(2.547)
    assert params.point_at_target is True


def test_standalone_eyeball_shader_without_feature_flag():
    params = eyes.extract_eye_params(
        "csgo_eyeball.vfx",
        {
            "g_tEyeAlbedo1": "characters/models/shared/materials/eyes/eyeball_brown_01_color.vtex",
            "g_tEyeMask1": "materials/default/default_eyemask.vtex",
        },
    )
    assert params is not None
    assert params.radius == pytest.approx(eyes.DEFAULT_RADIUS_INCHES * eyes.INCHES_TO_METERS)
    assert params.iris_size == pytest.approx(1.0)
    assert params.pupil_size == pytest.approx(0.15)


def test_non_eye_material_returns_none():
    assert eyes.extract_eye_params("csgo_character.vfx", {"F_CLOTH_SHADING": 1}) is None
    assert eyes.extract_eye_params("csgo_character.vfx", {"F_EYEBALLS": 1}) is None
    assert eyes.flatten_vmat_prop(None) == ("", "", {})


def test_iris_color_matrix_identity_grayscale_and_gray_invariance():
    ident = eyes.iris_color_matrix(0.0, 1.0)
    assert _mat3_vec(ident, (0.7, 0.4, 0.2)) == pytest.approx((0.7, 0.4, 0.2), abs=1e-6)

    desat = eyes.iris_color_matrix(0.0, 0.0)
    luma = 0.2125 * 0.8 + 0.7154 * 0.3 + 0.0721 * 0.1
    assert _mat3_vec(desat, (0.8, 0.3, 0.1)) == pytest.approx((luma, luma, luma), abs=1e-6)

    # Neutral grays lie on the (1, 1, 1) rotation axis and must stay unchanged for any hue/sat.
    shifted = eyes.iris_color_matrix(2.1, 0.4)
    assert _mat3_vec(shifted, (0.5, 0.5, 0.5)) == pytest.approx((0.5, 0.5, 0.5), abs=1e-6)

    # Full 2*pi hue rotation returns to identity.
    full_turn = eyes.iris_color_matrix(2.0 * math.pi, 1.0)
    assert _mat3_vec(full_turn, (0.6, 0.25, 0.1)) == pytest.approx((0.6, 0.25, 0.1), abs=1e-6)


def test_eye_frame_matrix_orientation_scale_and_walleye():
    radius = 0.01524
    pos_l = (0.035, -0.05, 1.85)
    target = (0.0, -2.5, 1.85)
    mat_l = eyes.eye_frame_matrix(pos_l, target, radius, walleye_deg=0.0)
    # Right-handed orthogonal basis scaled uniformly by radius.
    assert _mat4_det3(mat_l) == pytest.approx(radius**3, rel=1e-5)
    assert (mat_l[0][3], mat_l[1][3], mat_l[2][3]) == pytest.approx(pos_l)
    # Forward column points roughly along -Y toward target.
    assert mat_l[1][2] / radius < -0.99

    # Positive walleye rotates the left eye outward toward +X; negative rotates right eye toward -X.
    mat_l_wall = eyes.eye_frame_matrix(pos_l, target, radius, walleye_deg=10.0)
    assert mat_l_wall[0][2] > mat_l[0][2]

    pos_r = (-0.035, -0.05, 1.85)
    mat_r = eyes.eye_frame_matrix(pos_r, target, radius, walleye_deg=0.0)
    mat_r_wall = eyes.eye_frame_matrix(pos_r, target, radius, walleye_deg=-10.0)
    assert mat_r_wall[0][2] < mat_r[0][2]


def test_clamp_gaze_direction_limits_extreme_angles():
    clamped = eyes.clamp_gaze_direction((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))
    # Must be pulled back toward forward (-Y), well inside 90 degrees.
    assert clamped[1] < -0.5
    assert math.sqrt(sum(c * c for c in clamped)) == pytest.approx(1.0)


def test_project_iris_uv_center_and_iris_scale():
    assert eyes.project_iris_uv((0.0, 0.0, 1.0), 1.0) == pytest.approx((0.5, 0.5))
    # Larger iris_size pulls sample UVs closer to 0.5 so the iris covers more of the eyeball.
    u_small, _ = eyes.project_iris_uv((0.3, 0.0, math.sqrt(1.0 - 0.09)), 0.9)
    u_large, _ = eyes.project_iris_uv((0.3, 0.0, math.sqrt(1.0 - 0.09)), 1.3)
    assert abs(u_large - 0.5) < abs(u_small - 0.5)
