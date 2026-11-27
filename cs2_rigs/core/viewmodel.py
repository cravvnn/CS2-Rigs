"""Source 2 first-person viewmodel math, presets, and clip filtering. No Blender imports here."""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import clipdata

WEAPON_ARMS_MODEL = "weapons/models/shared/arms/weapon_arms.vmdl_c"
WEAPON_ARMS_BASE_CLIP = "animation/anims/viewmodel/rifle/rifle_ak/idle_ak"
VIEWMODEL_CLIP_PREFIX = "animation/anims/viewmodel/"

INCH_TO_METER = 0.0254
POV_CLIP_START = 0.01
POV_CLIP_END = 100.0
FOV_MIN = 54.0
FOV_MAX = 68.0
DEFAULT_FOV = 68.0

# Non-viewmodel-skeleton folders that live under animation/anims/viewmodel/ (chicken/egg props).
_NON_ARM_VIEWMODEL_FOLDERS = ("chicken", "egg")
VIEWMODEL_FAMILIES = ("rifle", "knife", "pistol", "grenade", "equipment", "arms")


@dataclass(frozen=True)
class ViewmodelPreset:
    key: str
    label: str
    description: str
    fov: float
    offset: tuple[float, float, float]  # (offset_x, offset_y, offset_z) in CS2 inches


PRESETS: dict[str, ViewmodelPreset] = {
    "classic": ViewmodelPreset(
        key="classic",
        label="Classic",
        description="CS2 Classic viewmodel (68° FOV, offset 2.5 / 0 / -1.5)",
        fov=68.0,
        offset=(2.5, 0.0, -1.5),
    ),
    "desktop": ViewmodelPreset(
        key="desktop",
        label="Desktop",
        description="CS2 Desktop viewmodel (60° FOV, offset 1 / 1 / -1)",
        fov=60.0,
        offset=(1.0, 1.0, -1.0),
    ),
    "custom": ViewmodelPreset(
        key="custom",
        label="Custom",
        description="Custom viewmodel FOV and camera-space offset",
        fov=68.0,
        offset=(2.5, 0.0, -1.5),
    ),
}
PRESET_ORDER = ("classic", "desktop", "custom")


def get_preset(key: str) -> ViewmodelPreset:
    return PRESETS.get(key, PRESETS["classic"])


def detect_preset(
    fov: float,
    offset_x: float,
    offset_y: float,
    offset_z: float,
    tol: float = 1e-3,
) -> str:
    """Return 'classic' or 'desktop' when values match a built-in preset, else 'custom'."""
    for key in ("classic", "desktop"):
        preset = PRESETS[key]
        ox, oy, oz = preset.offset
        if (
            abs(fov - preset.fov) <= tol
            and abs(offset_x - ox) <= tol
            and abs(offset_y - oy) <= tol
            and abs(offset_z - oz) <= tol
        ):
            return key
    return "custom"


def source2_fov_to_vertical_rad(fov_4x3_deg: float) -> float:
    """Convert Source 2 Hor+ 4:3 FOV (viewmodel_fov, 54-68 deg) to invariant vertical FOV in radians."""
    clamped = max(1.0, min(179.0, float(fov_4x3_deg)))
    return 2.0 * math.atan(0.75 * math.tan(math.radians(clamped) * 0.5))


def vertical_rad_to_source2_fov(vfov_rad: float) -> float:
    """Inverse of source2_fov_to_vertical_rad: vertical FOV in radians -> Source 2 4:3 FOV in degrees."""
    return math.degrees(2.0 * math.atan((4.0 / 3.0) * math.tan(float(vfov_rad) * 0.5)))


def horizontal_fov_deg(vfov_rad: float, aspect: float = 16.0 / 9.0) -> float:
    """Resulting horizontal FOV in degrees for a given vertical FOV (radians) and display aspect ratio."""
    return math.degrees(2.0 * math.atan(float(aspect) * math.tan(float(vfov_rad) * 0.5)))


def offset_inches_to_blender(offset_x: float, offset_y: float, offset_z: float) -> tuple[float, float, float]:
    """Map CS2 viewmodel_offset_x/y/z (inches) to Blender arms location (metres).

    In Blender Z-up with the POV camera looking down -Y (+Z up, -X right):
      +offset_x (right)   -> -X
      +offset_y (forward) -> -Y
      +offset_z (up)      -> +Z
    """
    return (
        -float(offset_x) * INCH_TO_METER,
        -float(offset_y) * INCH_TO_METER,
        float(offset_z) * INCH_TO_METER,
    )


def blender_to_offset_inches(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Inverse of offset_inches_to_blender: Blender arms location (metres) -> CS2 offset inches."""
    return (
        -float(x) / INCH_TO_METER,
        -float(y) / INCH_TO_METER,
        float(z) / INCH_TO_METER,
    )


def _clip_label(clip: str) -> str:
    prefix = clipdata.CLIP_PREFIX
    return clip[len(prefix):] if clip.startswith(prefix) else clip


def is_viewmodel_clip(clip: str) -> bool:
    """True for any clip under animation/anims/viewmodel/ (full path or label)."""
    return _clip_label(clip).startswith("viewmodel/")


def viewmodel_family_of_clip(clip: str) -> str:
    """'animation/anims/viewmodel/rifle/rifle_ak/idle_ak' -> 'rifle'; '' outside viewmodel clips."""
    parts = _clip_label(clip).split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "viewmodel" else ""


def is_viewmodel_arm_clip(clip: str, census: dict | None = None) -> bool:
    """True when `clip` targets the first-person arms skeleton (excludes chicken/egg viewmodel clips)."""
    if not is_viewmodel_clip(clip):
        return False
    full = clip if clip.startswith(clipdata.CLIP_PREFIX) else clipdata.CLIP_PREFIX + clip
    if census is not None:
        clips_map = census.get("clips", census)
        info = clips_map.get(full)
        if isinstance(info, dict) and "skeleton" in info:
            return info["skeleton"] == clipdata.VIEWMODEL
    return viewmodel_family_of_clip(clip) not in _NON_ARM_VIEWMODEL_FOLDERS


def clips_for_fp_weapon(clips: list[str], weapon, census: dict | None = None) -> list[str]:
    """First-person viewmodel clips for a held weapon: its own folder first, then family defaults,
    then shared arms clips. Takes full clip paths and excludes non-viewmodel-skeleton clips.
    """
    from . import catalog as catalog_mod

    vm_clips = [c for c in clips if is_viewmodel_arm_clip(c, census)]
    if weapon is None:
        return vm_clips
    family = catalog_mod.clip_family(weapon)
    base = f"{VIEWMODEL_CLIP_PREFIX}{family}/"
    folders = sorted({clipdata.folder_of(c) for c in vm_clips if c.startswith(base)})
    own = clipdata.weapon_folder(weapon.key, folders)
    return clipdata.clips_for_weapon(vm_clips, f"{base}_default_{family}", own, VIEWMODEL_CLIP_PREFIX + "arms")
