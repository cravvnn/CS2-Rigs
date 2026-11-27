"""Tests for pure-Python Studio Kit framing, lighting, cyclorama and turntable math."""

import math

import pytest

from core import studio


def _rot_matrix_xyz(euler):
    rx, ry, rz = euler
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Rz @ Ry @ Rx
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )


def _mul_mat_vec(mat, vec):
    return (
        mat[0][0] * vec[0] + mat[0][1] * vec[1] + mat[0][2] * vec[2],
        mat[1][0] * vec[0] + mat[1][1] * vec[1] + mat[1][2] * vec[2],
        mat[2][0] * vec[0] + mat[2][1] * vec[1] + mat[2][2] * vec[2],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(v):
    mag = math.sqrt(_dot(v, v))
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def test_presets_are_complete():
    assert set(studio.PRESET_ORDER) == set(studio.PRESETS.keys())
    for key in studio.PRESET_ORDER:
        preset = studio.get_preset(key)
        assert preset.key == key
        assert len(preset.lights) == 3
        roles = {light.role for light in preset.lights}
        assert roles == {"key_light", "fill_light", "rim_light"}
        assert preset.world.strength >= 0.0
        assert 0.0 <= preset.backdrop.roughness <= 1.0
    assert studio.get_preset("unknown_key").key == "studio"


def test_compute_bounds_and_fallback():
    assert studio.compute_bounds([]) == studio.DEFAULT_BOUNDS
    assert studio.compute_bounds([(1.0, 2.0, 3.0)]) == studio.DEFAULT_BOUNDS
    bounds = studio.compute_bounds([(-0.5, -1.0, 0.0), (0.5, 0.2, 1.8)])
    assert bounds.min_xyz == (-0.5, -1.0, 0.0)
    assert bounds.max_xyz == (0.5, 0.2, 1.8)
    assert math.isclose(bounds.height, 1.8)
    assert math.isclose(bounds.floor_z, 0.0)
    assert len(bounds.corners()) == 8


def test_look_at_euler_points_negative_z_at_target():
    target = (0.2, -0.1, 1.0)
    for loc in (
        (0.2, -4.0, 1.0),
        (2.5, -3.0, 2.2),
        (-3.0, 2.5, 1.5),
        (0.2, 4.0, 0.5),
        (0.2, -0.1, 5.0),
    ):
        euler = studio.look_at_euler_xyz(loc, target)
        rot = _rot_matrix_xyz(euler)
        # Camera looks along local -Z.
        world_forward = _mul_mat_vec(rot, (0.0, 0.0, -1.0))
        expected = _norm(_sub(target, loc))
        assert _dot(world_forward, expected) > 0.9999


@pytest.mark.parametrize("aspect", [16.0 / 9.0, 1.0, 9.0 / 16.0])
@pytest.mark.parametrize("forward", [(0.0, -1.0, 0.0), (-0.4, -0.9, 0.0), (1.0, 0.0, 0.0)])
def test_frame_camera_contains_all_corners_within_fill_ratio(aspect, forward):
    bounds = studio.Bounds((-0.35, -0.95, 0.0), (0.55, 0.45, 1.85))
    fill = 0.78
    cam = studio.frame_camera(bounds, forward, aspect_ratio=aspect, fill_ratio=fill)
    hfov, vfov = studio.camera_fov(cam.focal_length_mm, cam.sensor_width_mm, aspect)
    rot = _rot_matrix_xyz(cam.rotation_euler)
    right = _mul_mat_vec(rot, (1.0, 0.0, 0.0))
    up = _mul_mat_vec(rot, (0.0, 1.0, 0.0))
    fwd = _mul_mat_vec(rot, (0.0, 0.0, -1.0))

    max_ndc_x = 0.0
    max_ndc_y = 0.0
    for corner in bounds.corners():
        rel = _sub(corner, cam.location)
        depth = _dot(rel, fwd)
        assert depth > cam.clip_start
        assert depth < cam.clip_end
        ndc_x = abs(_dot(rel, right)) / (depth * math.tan(0.5 * hfov))
        ndc_y = abs(_dot(rel, up)) / (depth * math.tan(0.5 * vfov))
        max_ndc_x = max(max_ndc_x, ndc_x)
        max_ndc_y = max(max_ndc_y, ndc_y)

    assert max(max_ndc_x, max_ndc_y) <= fill + 1e-4
    assert max(max_ndc_x, max_ndc_y) >= fill - 0.05


def test_place_lights_scales_energy_with_subject_size():
    preset = studio.get_preset("studio")
    small = studio.Bounds((-0.3, -0.1, 0.0), (0.3, 0.1, 0.4))
    large = studio.Bounds((-1.2, -0.8, 0.0), (1.2, 0.8, 3.6))
    small_lights = studio.place_lights(preset, small)
    large_lights = studio.place_lights(preset, large)
    assert len(small_lights) == len(large_lights) == 3
    for s_light, l_light in zip(small_lights, large_lights):
        assert l_light.energy > s_light.energy
        assert l_light.size > s_light.size
        assert s_light.location[2] > small.floor_z
        assert l_light.location[2] > large.floor_z


def test_cyclorama_mesh_has_valid_inward_facing_geometry():
    bounds = studio.Bounds((-0.4, -0.5, -0.02), (0.4, 0.4, 1.85))
    cam = studio.frame_camera(bounds)
    spec = studio.cyclorama_spec(bounds, cam.distance)
    assert spec.floor_radius > cam.distance
    assert spec.floor_z < bounds.floor_z

    verts, faces = studio.cyclorama_mesh_data(spec)
    assert len(verts) > 50
    assert len(faces) > 50
    for face in faces:
        assert len(face) in (3, 4)
        for idx in face:
            assert 0 <= idx < len(verts)
        # Check face normal points upward (+Z) on floor or inward (-radial) on cove/wall.
        v0, v1, v2 = verts[face[0]], verts[face[1]], verts[face[2]]
        normal = _cross(_sub(v1, v0), _sub(v2, v0))
        cx = sum(verts[i][0] for i in face) / len(face)
        cy = sum(verts[i][1] for i in face) / len(face)
        cz = sum(verts[i][2] for i in face) / len(face)
        if cz < 1e-4:
            assert normal[2] > 0.0  # floor points up
        else:
            # Inward vector from face center toward axis (0, 0, cz).
            assert normal[0] * (-cx) + normal[1] * (-cy) >= -1e-6


def test_turntable_keyframes_and_angles():
    keys = studio.turntable_keyframes(1, 120)
    assert keys == [(1, 0.0), (121, pytest.approx(2.0 * math.pi))]
    assert studio.turntable_angle_at(1, 1, 120) == pytest.approx(0.0)
    assert studio.turntable_angle_at(61, 1, 120) == pytest.approx(math.pi)
    assert studio.turntable_angle_at(121, 1, 120) == pytest.approx(2.0 * math.pi)
    # Degenerate frame range falls back safely to a 120-frame span.
    fallback = studio.turntable_keyframes(10, 10)
    assert fallback[0] == (10, 0.0)
    assert fallback[1][0] == 130
    assert fallback[1][1] == pytest.approx(2.0 * math.pi)
    # Clockwise flips sign.
    cw = studio.turntable_keyframes(1, 100, clockwise=True)
    assert cw[1][1] == pytest.approx(-2.0 * math.pi)
