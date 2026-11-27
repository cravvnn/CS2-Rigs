"""Pure-Python geometry, lighting preset, camera framing and turntable math for the Studio Kit."""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

DEFAULT_FORWARD: Vec3 = (0.0, -1.0, 0.0)
DEFAULT_FOCAL_LENGTH_MM = 65.0
DEFAULT_SENSOR_WIDTH_MM = 36.0
DEFAULT_FILL_RATIO = 0.82
DEFAULT_AZIMUTH_DEG = -24.0
DEFAULT_ELEVATION_DEG = 5.0
REFERENCE_DISTANCE = 3.0


@dataclass(frozen=True)
class Bounds:
    """Axis-aligned world-space bounding box of a subject."""

    min_xyz: Vec3
    max_xyz: Vec3

    @property
    def center(self) -> Vec3:
        return (
            0.5 * (self.min_xyz[0] + self.max_xyz[0]),
            0.5 * (self.min_xyz[1] + self.max_xyz[1]),
            0.5 * (self.min_xyz[2] + self.max_xyz[2]),
        )

    @property
    def size(self) -> Vec3:
        return (
            max(0.0, self.max_xyz[0] - self.min_xyz[0]),
            max(0.0, self.max_xyz[1] - self.min_xyz[1]),
            max(0.0, self.max_xyz[2] - self.min_xyz[2]),
        )

    @property
    def width(self) -> float:
        return self.size[0]

    @property
    def depth(self) -> float:
        return self.size[1]

    @property
    def height(self) -> float:
        return self.size[2]

    @property
    def span(self) -> float:
        return max(self.width, self.depth, self.height)

    @property
    def radius(self) -> float:
        sx, sy, sz = self.size
        return 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)

    @property
    def floor_z(self) -> float:
        return self.min_xyz[2]

    @property
    def is_valid(self) -> bool:
        return self.span > 1e-4

    def corners(self) -> tuple[Vec3, ...]:
        x0, y0, z0 = self.min_xyz
        x1, y1, z1 = self.max_xyz
        return (
            (x0, y0, z0),
            (x1, y0, z0),
            (x0, y1, z0),
            (x1, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x0, y1, z1),
            (x1, y1, z1),
        )


# Standard standing CS2 agent dimensions, used when adding a studio to an empty scene.
DEFAULT_BOUNDS = Bounds((-0.45, -0.35, 0.0), (0.45, 0.35, 1.85))


@dataclass(frozen=True)
class CameraPlacement:
    """Computed studio camera transform and lens settings."""

    location: Vec3
    target: Vec3
    rotation_euler: Vec3
    focal_length_mm: float
    sensor_width_mm: float
    clip_start: float
    clip_end: float
    distance: float


@dataclass(frozen=True)
class LightSpec:
    """Subject-relative definition of one area light in a lighting preset."""

    role: str
    label: str
    azimuth_deg: float  # degrees around +Z from the subject's front direction
    elevation_deg: float  # degrees above horizontal
    distance_scale: float  # multiplied by subject span
    size_scale: float  # softbox width as multiple of subject span
    size_y_scale: float  # softbox height as multiple of subject span
    energy: float  # Watts at REFERENCE_DISTANCE
    color: Vec3  # linear RGB
    spread_deg: float  # area light beam spread in degrees


@dataclass(frozen=True)
class LightPlacement:
    """World-space placement and intensity for one studio area light."""

    role: str
    label: str
    location: Vec3
    target: Vec3
    rotation_euler: Vec3
    size: float
    size_y: float
    energy: float
    color: Vec3
    spread_rad: float


@dataclass(frozen=True)
class WorldSpec:
    """Subtle studio ambient fill."""

    color: Vec3  # linear RGB
    strength: float


@dataclass(frozen=True)
class BackdropSpec:
    """Surface appearance of the studio cyclorama."""

    color: Vec3  # linear RGB
    roughness: float
    specular: float


@dataclass(frozen=True)
class LightingPreset:
    """A complete lighting, world and backdrop look."""

    key: str
    label: str
    description: str
    lights: tuple[LightSpec, ...]
    world: WorldSpec
    backdrop: BackdropSpec


@dataclass(frozen=True)
class CycloramaSpec:
    """Dimensions of the 360-degree seamless studio cyclorama bowl."""

    center_xy: tuple[float, float]
    floor_z: float
    floor_radius: float
    cove_radius: float
    wall_height: float
    radial_segments: int = 64
    cove_segments: int = 16


PRESET_ORDER: tuple[str, ...] = ("studio", "dark", "rim")

PRESETS: dict[str, LightingPreset] = {
    "studio": LightingPreset(
        key="studio",
        label="Studio",
        description="Balanced 3-point softbox lighting on a clean neutral studio backdrop",
        lights=(
            LightSpec(
                role="key_light",
                label="CS2 Studio Key",
                azimuth_deg=36.0,
                elevation_deg=30.0,
                distance_scale=1.75,
                size_scale=0.90,
                size_y_scale=1.20,
                energy=250.0,
                color=(1.0, 0.96, 0.91),
                spread_deg=125.0,
            ),
            LightSpec(
                role="fill_light",
                label="CS2 Studio Fill",
                azimuth_deg=-54.0,
                elevation_deg=18.0,
                distance_scale=1.95,
                size_scale=1.30,
                size_y_scale=1.30,
                energy=95.0,
                color=(0.88, 0.94, 1.0),
                spread_deg=140.0,
            ),
            LightSpec(
                role="rim_light",
                label="CS2 Studio Rim",
                azimuth_deg=-148.0,
                elevation_deg=34.0,
                distance_scale=1.65,
                size_scale=0.65,
                size_y_scale=1.35,
                energy=210.0,
                color=(0.94, 0.97, 1.0),
                spread_deg=85.0,
            ),
        ),
        world=WorldSpec(color=(0.075, 0.085, 0.10), strength=0.22),
        backdrop=BackdropSpec(color=(0.058, 0.064, 0.074), roughness=0.88, specular=0.16),
    ),
    "dark": LightingPreset(
        key="dark",
        label="Dark",
        description="High-contrast moody studio lighting on a deep charcoal backdrop",
        lights=(
            LightSpec(
                role="key_light",
                label="CS2 Studio Key",
                azimuth_deg=42.0,
                elevation_deg=34.0,
                distance_scale=1.70,
                size_scale=0.62,
                size_y_scale=1.05,
                energy=270.0,
                color=(1.0, 0.95, 0.89),
                spread_deg=72.0,
            ),
            LightSpec(
                role="fill_light",
                label="CS2 Studio Fill",
                azimuth_deg=-58.0,
                elevation_deg=14.0,
                distance_scale=2.10,
                size_scale=1.20,
                size_y_scale=1.20,
                energy=30.0,
                color=(0.76, 0.86, 1.0),
                spread_deg=115.0,
            ),
            LightSpec(
                role="rim_light",
                label="CS2 Studio Rim",
                azimuth_deg=-142.0,
                elevation_deg=30.0,
                distance_scale=1.55,
                size_scale=0.45,
                size_y_scale=1.30,
                energy=280.0,
                color=(0.82, 0.91, 1.0),
                spread_deg=64.0,
            ),
        ),
        world=WorldSpec(color=(0.022, 0.026, 0.036), strength=0.05),
        backdrop=BackdropSpec(color=(0.012, 0.014, 0.018), roughness=0.86, specular=0.18),
    ),
    "rim": LightingPreset(
        key="rim",
        label="Rim",
        description="Dramatic dual-rim edge lighting with a soft frontal fill for weapon and silhouette showcase",
        lights=(
            LightSpec(
                role="key_light",
                label="CS2 Studio Key",
                azimuth_deg=126.0,
                elevation_deg=24.0,
                distance_scale=1.55,
                size_scale=0.45,
                size_y_scale=1.40,
                energy=380.0,
                color=(1.0, 0.74, 0.45),
                spread_deg=56.0,
            ),
            LightSpec(
                role="fill_light",
                label="CS2 Studio Fill",
                azimuth_deg=-10.0,
                elevation_deg=20.0,
                distance_scale=2.05,
                size_scale=1.15,
                size_y_scale=1.10,
                energy=42.0,
                color=(0.94, 0.97, 1.0),
                spread_deg=110.0,
            ),
            LightSpec(
                role="rim_light",
                label="CS2 Studio Rim",
                azimuth_deg=-128.0,
                elevation_deg=26.0,
                distance_scale=1.55,
                size_scale=0.45,
                size_y_scale=1.40,
                energy=360.0,
                color=(0.45, 0.78, 1.0),
                spread_deg=56.0,
            ),
        ),
        world=WorldSpec(color=(0.020, 0.026, 0.038), strength=0.05),
        backdrop=BackdropSpec(color=(0.014, 0.017, 0.024), roughness=0.90, specular=0.12),
    ),
}


def get_preset(key: str) -> LightingPreset:
    return PRESETS.get(key, PRESETS["studio"])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalize(v: Vec3, fallback: Vec3 = DEFAULT_FORWARD) -> Vec3:
    mag = _length(v)
    if mag <= 1e-8:
        return fallback
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def _rotate_z(v: Vec3, angle_rad: float) -> Vec3:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c, v[2])


def compute_bounds(points) -> Bounds:
    """Compute a world-space AABB from an iterable of 3D points, falling back to DEFAULT_BOUNDS."""
    it = iter(points)
    first = next(it, None)
    if first is None:
        return DEFAULT_BOUNDS
    min_x = max_x = float(first[0])
    min_y = max_y = float(first[1])
    min_z = max_z = float(first[2])
    for pt in it:
        x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
        if x < min_x:
            min_x = x
        elif x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        elif y > max_y:
            max_y = y
        if z < min_z:
            min_z = z
        elif z > max_z:
            max_z = z
    bounds = Bounds((min_x, min_y, min_z), (max_x, max_y, max_z))
    return bounds if bounds.is_valid else DEFAULT_BOUNDS


def normalize_forward_xy(vec: Vec3 | None) -> Vec3:
    """Project a direction onto the XY ground plane and normalize it."""
    if vec is None:
        return DEFAULT_FORWARD
    return _normalize((float(vec[0]), float(vec[1]), 0.0), DEFAULT_FORWARD)


def look_at_euler_xyz(location: Vec3, target: Vec3, up: Vec3 = (0.0, 0.0, 1.0)) -> Vec3:
    """Blender XYZ Euler angles for a camera or area light at `location` aiming along -Z at `target`."""
    z_axis = _normalize(_sub(location, target), (0.0, -1.0, 0.0))
    # Handle near-vertical look directions cleanly.
    ref_up = up if abs(_dot(z_axis, up)) < 0.999 else (0.0, 1.0, 0.0)
    x_axis = _normalize(_cross(ref_up, z_axis), (1.0, 0.0, 0.0))
    y_axis = _normalize(_cross(z_axis, x_axis), (0.0, 0.0, 1.0))

    # Rotation matrix columns are [x_axis, y_axis, z_axis]; decompose R = Rz @ Ry @ Rx.
    r20 = x_axis[2]
    sin_y = max(-1.0, min(1.0, -r20))
    ry = math.asin(sin_y)
    if abs(math.cos(ry)) > 1e-6:
        rx = math.atan2(y_axis[2], z_axis[2])
        rz = math.atan2(x_axis[1], x_axis[0])
    else:
        rx = math.atan2(-z_axis[1], y_axis[1])
        rz = 0.0
    return (rx, ry, rz)


def camera_fov(focal_mm: float, sensor_width_mm: float, aspect_ratio: float) -> tuple[float, float]:
    """Horizontal and vertical field of view in radians, matching Blender's AUTO sensor fit."""
    focal = max(1.0, float(focal_mm))
    sensor = max(1.0, float(sensor_width_mm))
    aspect = max(0.1, float(aspect_ratio))
    if aspect >= 1.0:
        sensor_w = sensor
        sensor_h = sensor / aspect
    else:
        sensor_h = sensor
        sensor_w = sensor * aspect
    hfov = 2.0 * math.atan(sensor_w / (2.0 * focal))
    vfov = 2.0 * math.atan(sensor_h / (2.0 * focal))
    return hfov, vfov


def subject_target(bounds: Bounds) -> Vec3:
    """Optical focus center of the subject (slightly above geometric mid-height)."""
    cx, cy, _ = bounds.center
    target_z = bounds.floor_z + 0.52 * bounds.height
    return (cx, cy, target_z)


def _direction_from_angles(forward: Vec3, azimuth_deg: float, elevation_deg: float) -> Vec3:
    """Unit vector pointing from target toward a camera or light at (azimuth, elevation) relative to `forward`."""
    horiz = _rotate_z(normalize_forward_xy(forward), math.radians(azimuth_deg))
    elev = math.radians(elevation_deg)
    cos_e, sin_e = math.cos(elev), math.sin(elev)
    return _normalize((horiz[0] * cos_e, horiz[1] * cos_e, sin_e), DEFAULT_FORWARD)


def frame_camera(
    bounds: Bounds,
    forward: Vec3 = DEFAULT_FORWARD,
    *,
    focal_mm: float = DEFAULT_FOCAL_LENGTH_MM,
    sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM,
    aspect_ratio: float = 16.0 / 9.0,
    fill_ratio: float = DEFAULT_FILL_RATIO,
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG,
    elevation_deg: float = DEFAULT_ELEVATION_DEG,
) -> CameraPlacement:
    """Place a studio camera so every corner of `bounds` fits cleanly inside `fill_ratio` of the frame."""
    fill = max(0.2, min(0.95, float(fill_ratio)))
    target = subject_target(bounds)
    to_cam = _direction_from_angles(forward, azimuth_deg, elevation_deg)
    # Camera basis: z_axis points from target toward camera; x_axis is right, y_axis is up.
    up = (0.0, 0.0, 1.0)
    ref_up = up if abs(_dot(to_cam, up)) < 0.999 else (0.0, 1.0, 0.0)
    right = _normalize(_cross(ref_up, to_cam), (1.0, 0.0, 0.0))
    cam_up = _normalize(_cross(to_cam, right), (0.0, 0.0, 1.0))

    hfov, vfov = camera_fov(focal_mm, sensor_width_mm, aspect_ratio)
    tan_h = math.tan(0.5 * hfov) * fill
    tan_v = math.tan(0.5 * vfov) * fill

    # Require every corner (rx, ry, rz in camera coords relative to target) to stay inside the frustum.
    distance = max(bounds.span * 0.8, 0.35)
    for corner in bounds.corners():
        rel = _sub(corner, target)
        rx = abs(_dot(rel, right))
        ry = abs(_dot(rel, cam_up))
        rz = _dot(rel, to_cam)  # positive when corner is closer to camera than target
        req_h = rz + rx / tan_h
        req_v = rz + ry / tan_v
        distance = max(distance, req_h, req_v)

    location = _add(target, _scale(to_cam, distance))
    rotation = look_at_euler_xyz(location, target)
    clip_start = max(0.01, min(0.1, distance * 0.02))
    clip_end = max(100.0, distance * 25.0)
    return CameraPlacement(
        location=location,
        target=target,
        rotation_euler=rotation,
        focal_length_mm=focal_mm,
        sensor_width_mm=sensor_width_mm,
        clip_start=clip_start,
        clip_end=clip_end,
        distance=distance,
    )


def place_lights(
    preset: LightingPreset,
    bounds: Bounds,
    forward: Vec3 = DEFAULT_FORWARD,
) -> list[LightPlacement]:
    """Compute world-space 3-point light placements scaled to `bounds` with inverse-square energy scaling."""
    span = max(bounds.span, 0.6)
    target = subject_target(bounds)
    placements: list[LightPlacement] = []
    for spec in preset.lights:
        direction = _direction_from_angles(forward, spec.azimuth_deg, spec.elevation_deg)
        distance = span * spec.distance_scale
        location = _add(target, _scale(direction, distance))
        # Ensure lights stay comfortably above the studio floor.
        min_z = bounds.floor_z + 0.35 * span
        if location[2] < min_z:
            location = (location[0], location[1], min_z)
        actual_dist = max(0.25, _length(_sub(location, target)))
        energy = spec.energy * (actual_dist / REFERENCE_DISTANCE) ** 2
        placements.append(
            LightPlacement(
                role=spec.role,
                label=spec.label,
                location=location,
                target=target,
                rotation_euler=look_at_euler_xyz(location, target),
                size=span * spec.size_scale,
                size_y=span * spec.size_y_scale,
                energy=energy,
                color=spec.color,
                spread_rad=math.radians(spec.spread_deg),
            )
        )
    return placements


def cyclorama_spec(bounds: Bounds, camera_distance: float) -> CycloramaSpec:
    """Size a 360-degree seamless studio bowl so camera and lights stay inside the flat floor."""
    span = max(bounds.span, 1.0)
    cx, cy, _ = bounds.center
    floor_radius = max(camera_distance * 1.45, span * 3.2)
    cove_radius = max(span * 1.35, floor_radius * 0.32)
    wall_height = max(span * 4.2, cove_radius + span * 2.5)
    return CycloramaSpec(
        center_xy=(cx, cy),
        floor_z=bounds.floor_z - 0.001,
        floor_radius=floor_radius,
        cove_radius=cove_radius,
        wall_height=wall_height,
    )


def cyclorama_mesh_data(spec: CycloramaSpec) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """Generate vertices and faces (in local coordinates around (0, 0, 0)) for a 360-degree studio bowl.

    Face winding produces normals pointing upward on the floor and inward on the cove and wall.
    """
    n_rad = max(12, int(spec.radial_segments))
    n_cove = max(4, int(spec.cove_segments))

    # Build (radius, z) profile rings from inner floor out through the quarter-circle cove up the wall.
    rings: list[tuple[float, float]] = [
        (spec.floor_radius * 0.35, 0.0),
        (spec.floor_radius * 0.72, 0.0),
        (spec.floor_radius, 0.0),
    ]
    for step in range(1, n_cove + 1):
        angle = (0.5 * math.pi) * (step / n_cove)
        r = spec.floor_radius + spec.cove_radius * math.sin(angle)
        z = spec.cove_radius * (1.0 - math.cos(angle))
        rings.append((r, z))
    outer_r = spec.floor_radius + spec.cove_radius
    if spec.wall_height > spec.cove_radius + 1e-4:
        rings.append((outer_r, spec.wall_height))

    vertices: list[Vec3] = [(0.0, 0.0, 0.0)]  # center floor vertex (index 0)
    cos_sin = [
        (math.cos(2.0 * math.pi * j / n_rad), math.sin(2.0 * math.pi * j / n_rad))
        for j in range(n_rad)
    ]
    for radius, z in rings:
        for c, s in cos_sin:
            vertices.append((radius * c, radius * s, z))

    faces: list[tuple[int, ...]] = []
    # Center fan (normals point +Z).
    for j in range(n_rad):
        j_next = (j + 1) % n_rad
        faces.append((0, 1 + j, 1 + j_next))

    # Quad strips between consecutive rings (normals point +Z on floor, inward on cove and wall).
    for ring_idx in range(len(rings) - 1):
        base0 = 1 + ring_idx * n_rad
        base1 = 1 + (ring_idx + 1) * n_rad
        for j in range(n_rad):
            j_next = (j + 1) % n_rad
            faces.append((base0 + j, base1 + j, base1 + j_next, base0 + j_next))

    return vertices, faces


def turntable_keyframes(
    frame_start: int,
    frame_end: int,
    *,
    turns: float = 1.0,
    clockwise: bool = False,
) -> list[tuple[int, float]]:
    """Keyframes (frame, z_radians) for a seamless linear 360-degree loop across [frame_start, frame_end].

    The end key sits at `frame_end + 1` so looping back to `frame_start` has no duplicate frame.
    """
    start = int(frame_start)
    end = int(frame_end)
    if end <= start:
        end = start + 119
    direction = -1.0 if clockwise else 1.0
    total_angle = direction * 2.0 * math.pi * float(turns)
    return [(start, 0.0), (end + 1, total_angle)]


def turntable_angle_at(
    frame: float,
    frame_start: int,
    frame_end: int,
    *,
    turns: float = 1.0,
    clockwise: bool = False,
) -> float:
    """Linear turntable Z-angle in radians at `frame`."""
    (f0, a0), (f1, a1) = turntable_keyframes(frame_start, frame_end, turns=turns, clockwise=clockwise)
    t = (float(frame) - f0) / (f1 - f0)
    return a0 + t * (a1 - a0)
