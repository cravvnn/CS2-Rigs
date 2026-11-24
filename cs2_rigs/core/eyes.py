"""Pure maths and material-parameter parsing for CS2 eyeball shading. No Blender imports here.

Counter-Strike 2 renders agent eyes either as a layer on `csgo_character.vfx`
(`F_EYEBALLS = 1`) or standalone with `csgo_eyeball.vfx`. Both shaders share the
same parameters and maths:
- Each eyeball is projected in its own orthonormal frame `(side, up, forward)`
  aimed at `eye_target` with optional per-eye walleye yaw and a 40-degree cone
  clamp.
- The iris UV is warped from the projected sphere normal by `g_flEyeIrisSize1`,
  sampled from `g_tEyeAlbedo1` (RGB = sclera + iris, A = linear iris mask).
- Hue shift is a Rodrigues rotation around `(1, 1, 1) / sqrt(3)` in linear RGB,
  followed by Rec. 709 saturation, both masked to the iris alpha.
- The pupil is a smoothstep falloff at radius `g_flEyePupilSize1 * g_flEyeIrisSize1`,
  and `g_tEyeMask1` (red channel) blends the eyeball layer over the head material.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .glb import Matrix4

EYEBALL_SHADER = "csgo_eyeball.vfx"
EYEBALL_FEATURE = "F_EYEBALLS"
INCHES_TO_METERS = 0.0254
DEFAULT_RADIUS_INCHES = 0.6
DEFAULT_IRIS_SIZE = 1.0
DEFAULT_PUPIL_SIZE = 0.15
DEFAULT_SATURATION = 1.0
PUPIL_FEATHER = 0.03
EYE_ROUGHNESS = 0.1
MAX_GAZE_RAD = math.radians(40.0)
LUMA_WEIGHTS = (0.2125, 0.7154, 0.0721)

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]


@dataclass(frozen=True)
class EyeParams:
    """Resolved shader parameters for one agent eye material."""

    albedo_texture: str
    mask_texture: str
    radius: float  # eyeball radius in metres
    iris_size: float
    pupil_size: float  # effective pupil radius (g_flEyePupilSize1 * g_flEyeIrisSize1)
    hue_shift: float  # radians around (1, 1, 1) / sqrt(3)
    saturation: float
    walleye_l: float  # degrees
    walleye_r: float  # degrees
    point_at_target: bool = True


def _clean_resource_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return value.replace("\\", "/").removesuffix("_c")


def _float_param(values: Mapping[str, object], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def flatten_vmat_prop(vmat: Mapping[str, object] | None) -> tuple[str, str, dict[str, object]]:
    """Extract (material_path, shader_name, flat_params) from a glTF `vmat` metadata mapping."""
    if vmat is None or not hasattr(vmat, "get"):
        return "", "", {}
    name = _clean_resource_path(vmat.get("Name", ""))
    shader = str(vmat.get("ShaderName") or "")
    flat: dict[str, object] = {}
    for group_name in ("IntParams", "FloatParams", "VectorParams", "TextureParams", "StringAttributes"):
        group = vmat.get(group_name)
        if group is None or not hasattr(group, "items"):
            continue
        for key, value in group.items():
            flat[str(key)] = tuple(value) if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)) else value
    return name, shader, flat


def is_eye_material(shader_name: str, values: Mapping[str, object]) -> bool:
    """True when a game material uses the eyeball shader or enables the character eyeball layer."""
    has_layer = shader_name == EYEBALL_SHADER or bool(values.get(EYEBALL_FEATURE))
    return has_layer and bool(_clean_resource_path(values.get("g_tEyeAlbedo1")))


def extract_eye_params(shader_name: str, values: Mapping[str, object]) -> EyeParams | None:
    """Parse eye shader parameters from a flat material-values dict, or None if not an eye material."""
    if not is_eye_material(shader_name, values):
        return None
    albedo = _clean_resource_path(values.get("g_tEyeAlbedo1"))
    mask = _clean_resource_path(values.get("g_tEyeMask1"))
    radius_in = max(_float_param(values, "g_flEyeBallRadius1", DEFAULT_RADIUS_INCHES), 0.05)
    iris_size = _float_param(values, "g_flEyeIrisSize1", DEFAULT_IRIS_SIZE)
    raw_pupil = _float_param(values, "g_flEyePupilSize1", DEFAULT_PUPIL_SIZE)
    # The shader evaluates `Expression(this * g_flEyeIrisSize1)` on g_flEyePupilSize1.
    pupil_size = max(raw_pupil * iris_size, 0.0)
    hue_shift = _float_param(values, "g_flEyeHueShift1", 0.0)
    saturation = _float_param(values, "g_flEyeSaturation1", DEFAULT_SATURATION)
    walleye_l = _float_param(values, "g_flEyeBallWalleyeL1", 0.0)
    walleye_r = _float_param(values, "g_flEyeBallWalleyeR1", 0.0)
    point_raw = values.get("g_bPointAtEyeTarget1")
    point_at_target = True if point_raw is None else bool(point_raw)
    return EyeParams(
        albedo_texture=albedo,
        mask_texture=mask,
        radius=radius_in * INCHES_TO_METERS,
        iris_size=iris_size,
        pupil_size=pupil_size,
        hue_shift=hue_shift,
        saturation=saturation,
        walleye_l=walleye_l,
        walleye_r=walleye_r,
        point_at_target=point_at_target,
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _normalize(v: Vec3, fallback: Vec3 = (0.0, -1.0, 0.0)) -> Vec3:
    length = math.sqrt(_dot(v, v))
    if length < 1e-12:
        return fallback
    return (v[0] / length, v[1] / length, v[2] / length)


def rotate_around_axis(vec: Vec3, axis: Vec3, angle_rad: float) -> Vec3:
    """Right-handed Rodrigues rotation of `vec` around unit `axis` by `angle_rad`."""
    if abs(angle_rad) < 1e-12:
        return vec
    k = _normalize(axis, (0.0, 0.0, 1.0))
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    k_dot_v = _dot(k, vec)
    k_cross_v = _cross(k, vec)
    return (
        vec[0] * c + k_cross_v[0] * s + k[0] * k_dot_v * (1.0 - c),
        vec[1] * c + k_cross_v[1] * s + k[1] * k_dot_v * (1.0 - c),
        vec[2] * c + k_cross_v[2] * s + k[2] * k_dot_v * (1.0 - c),
    )


def clamp_gaze_direction(direction: Vec3, bind_fwd: Vec3, bind_up: Vec3, max_angle: float = MAX_GAZE_RAD) -> Vec3:
    """Clamp an eye look direction to the shader's dual 40-degree cones around bind_fwd +/- 0.5*bind_up."""
    d = _normalize(direction, bind_fwd)
    fwd = _normalize(bind_fwd)
    up = _normalize(bind_up, (0.0, 0.0, 1.0))
    for sign in (0.5, -0.5):
        ref = _normalize(_add(fwd, _scale(up, sign)), fwd)
        cos_ang = max(-1.0, min(1.0, _dot(d, ref)))
        if math.acos(cos_ang) > max_angle:
            axis = _cross(ref, d)
            if _dot(axis, axis) > 1e-12:
                d = _normalize(rotate_around_axis(ref, axis, max_angle), d)
    return d


def eye_frame_matrix(
    eye_pos: Vec3,
    target_pos: Vec3 | None,
    radius: float,
    walleye_deg: float = 0.0,
    bind_fwd: Vec3 = (0.0, -1.0, 0.0),
    bind_up: Vec3 = (0.0, 0.0, 1.0),
    point_at_target: bool = True,
) -> Matrix4:
    """4x4 armature-space matrix for one eyeball frame, scaled by `radius`.

    Columns are (side * radius, up * radius, forward * radius, eye_pos), so
    `inverse(M) @ P` transforms any point `P` on the eyeball into unit-sphere
    local coordinates `(dot(d, side), dot(d, up), dot(d, forward))`.
    """
    up_unit = _normalize(bind_up, (0.0, 0.0, 1.0))
    fwd_unit = _normalize(bind_fwd, (0.0, -1.0, 0.0))
    if point_at_target and target_pos is not None:
        to_target = _sub(target_pos, eye_pos)
        if _dot(to_target, to_target) > 1e-12:
            fwd_unit = _normalize(to_target, fwd_unit)
        if walleye_deg:
            fwd_unit = _normalize(rotate_around_axis(fwd_unit, up_unit, math.radians(walleye_deg)), fwd_unit)
        fwd_unit = clamp_gaze_direction(fwd_unit, bind_fwd, up_unit)

    side_unit = _normalize(_cross(up_unit, fwd_unit), (1.0, 0.0, 0.0))
    ortho_up = _normalize(_cross(fwd_unit, side_unit), up_unit)

    sx, sy, sz = _scale(side_unit, radius)
    ux, uy, uz = _scale(ortho_up, radius)
    fx, fy, fz = _scale(fwd_unit, radius)
    tx, ty, tz = eye_pos
    return [
        [sx, ux, fx, tx],
        [sy, uy, fy, ty],
        [sz, uz, fz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def iris_color_matrix(hue_shift: float, saturation: float) -> Mat3:
    """Combined 3x3 linear-RGB matrix for the shader's iris hue rotation and saturation."""
    c = math.cos(hue_shift)
    s = math.sin(hue_shift)
    t = (1.0 - c) / 3.0
    u = s / math.sqrt(3.0)
    m_hue = (
        (c + t, t - u, t + u),
        (t + u, c + t, t - u),
        (t - u, t + u, c + t),
    )
    wr, wg, wb = LUMA_WEIGHTS
    inv_sat = 1.0 - saturation
    m_sat = (
        (inv_sat * wr + saturation, inv_sat * wg, inv_sat * wb),
        (inv_sat * wr, inv_sat * wg + saturation, inv_sat * wb),
        (inv_sat * wr, inv_sat * wg, inv_sat * wb + saturation),
    )
    rows = []
    for i in range(3):
        rows.append(
            tuple(sum(m_sat[i][k] * m_hue[k][j] for k in range(3)) for j in range(3))
        )
    return (rows[0], rows[1], rows[2])


def project_iris_uv(normal_local: Vec3, iris_size: float) -> tuple[float, float]:
    """Blender UV (origin bottom-left) for a unit sphere normal in eye-local `(side, up, fwd)` space."""
    nx, ny, _ = _normalize(normal_local, (0.0, 0.0, 1.0))
    r = min(1.0, math.hypot(nx, ny))
    scale = 2.0 - iris_size
    factor = scale * (1.0 - r) + r
    return (nx * factor * 0.5 + 0.5, ny * factor * 0.5 + 0.5)
