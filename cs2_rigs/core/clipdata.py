"""What each animation clip is, how well it loads onto an agent, and its stick-figure preview.

Three things live here, none of them needing Blender:
- The census: facts read from each clip's DATA block (skeleton, frames, duration,
  additive or not, secondary skeletons), streamed from the converter folder by folder.
- clip_status.json: one verdict per clip, written by the clip sweep or the
  Build Clip Previews job, read by the clip browser.
- clip_previews/<digest>.json: a few major bones sampled over the clip, small
  enough for the browser to draw without importing anything.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import proc
from .converter import Converter

CLIP_PREFIX = "animation/anims/"
CLIP_EXTENSION = "vnmclip_c"
NON_ADDITIVE_MARK = ".vnmclip+non_additive"
CENSUS_VERSION = 1
STATUS_VERSION = 1
PREVIEW_VERSION = 1
PREVIEW_FPS = 15

WORLDMODEL, VIEWMODEL, CHICKEN, OTHER = "worldmodel", "viewmodel", "chicken", "other"
VERDICTS = (
    "ok", "additive", "no-animation", "collapsed", "length-mismatch",
    "other-skeleton", "converter-error", "timeout", "unchecked",
)

# How far a parent-to-child bone distance may stray from the agent's rest length.
# Some clips move bones in the game data itself: jumps made on other proportions
# hold the spine up to ~11% longer or shorter, and flinches jolt the hit limb up to
# ~22% for a moment. That is noted. A collapsed pose (an additive clip played
# alone) folds bones to nothing, and first-person arm clips pull the shoulders far
# from the body; both are well past the limit.
BONE_LENGTH_TOLERANCE = 0.02
BONE_LENGTH_LIMIT = 0.25
SCALE_TOLERANCE = 0.02
MIN_PELVIS_TO_HEAD = 0.3  # metres; an upright or crouched agent is ~0.7
FRAME_TOLERANCE = 1
HAND_GAP_NOTE = 0.15  # metres the weapon bone moves relative to the right hand before it is noted


def skeleton_kind(resource: str) -> str:
    """'animation/skeletons/characters/worldmodel.vnmskel' -> 'worldmodel'."""
    name = resource.replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0]
    if name in (WORLDMODEL, VIEWMODEL, CHICKEN):
        return name
    return OTHER


def category(clip: str) -> str:
    """The top two folders under animation/anims/: 'world/rifle', 'ui_anims/main_menu', or 'egg'."""
    folders = clip.removeprefix(CLIP_PREFIX).split("/")[:-1]
    return "/".join(folders[:2])


def folder_of(clip: str) -> str:
    return clip.rsplit("/", 1)[0]


def base_of(clip: str) -> str:
    """A generated '+non_additive' clip -> the clip it was made from; others unchanged."""
    return clip.split(NON_ADDITIVE_MARK, 1)[0]


# --- Census -----------------------------------------------------------------

_HEADER = re.compile(r"^\[\d+/\d+\] (?P<path>.+)\.vnmclip_c\s*$")
_REFERENCE = re.compile(r"^\t[0-9A-Fa-f]{16}\s+(?P<path>\S+)")
_TOP_KEY = re.compile(r"^\t(?P<key>m_\w+) =\s?(?P<value>.*?)\s*$")
_RESOURCE = re.compile(r'resource(?:_name)?:"(?P<path>[^"]*)"')
_SECONDARY_SKELETON = re.compile(r'^\t\t\tm_skeleton = resource(?:_name)?:"(?P<path>[^"]*)"')
_ROOT_SPEED = re.compile(r"^\t\tm_flAverageLinearVelocity = (?P<value>\S+)")
_DATA_BLOCK = '--- Data for block "DATA" ---'


def _new_entry() -> dict:
    return {
        "skeleton": OTHER, "skeleton_path": "", "frames": 0, "duration": 0.0, "additive": False,
        "secondary": [], "non_additive": "", "root_speed": 0.0,
    }


def _number(text: str, kind=float):
    try:
        return kind(text)
    except ValueError:
        return kind(0)


def parse_clip_dump(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    """Read the converter's `-b DATA` text for clips, one line at a time.

    Yields (clip path without '.vnmclip_c', facts). Only a handful of top-level
    fields are kept, so a folder's dump of tens of megabytes is never held whole.
    """
    path, entry, in_data, section = "", None, False, ""
    for raw in lines:
        line = raw.rstrip("\r\n")
        header = _HEADER.match(line)
        if header:
            if entry is not None:
                yield path, entry
            path, entry, in_data, section = header.group("path"), _new_entry(), False, ""
            continue
        if entry is None:
            continue
        if not in_data:
            if line.startswith(_DATA_BLOCK):
                in_data = True
                continue
            ref = _REFERENCE.match(line)
            # An additive clip names the non-additive version made from it.
            if ref and ref.group("path").endswith(NON_ADDITIVE_MARK + ".vnmclip"):
                entry["non_additive"] = ref.group("path").removesuffix(".vnmclip")
            continue
        top = _TOP_KEY.match(line)
        if top:
            section, value = top.group("key"), top.group("value")
            if section == "m_skeleton":
                resource = _RESOURCE.search(value)
                entry["skeleton_path"] = resource.group("path") if resource else ""
                entry["skeleton"] = skeleton_kind(entry["skeleton_path"])
            elif section == "m_nNumFrames":
                entry["frames"] = _number(value, int)
            elif section == "m_flDuration":
                entry["duration"] = _number(value)
            elif section == "m_bIsAdditive":
                entry["additive"] = value == "true"
            continue
        if section == "m_secondaryAnimations":
            secondary = _SECONDARY_SKELETON.match(line)
            if secondary:
                entry["secondary"].append(secondary.group("path"))
        elif section == "m_rootMotion":
            speed = _ROOT_SPEED.match(line)
            if speed:
                entry["root_speed"] = _number(speed.group("value"))
    if entry is not None:
        yield path, entry


def fold_generated(found: dict[str, dict]) -> dict[str, dict]:
    """Keep the clips people choose from; note each '+non_additive' version on its own clip."""
    census = {path: dict(entry) for path, entry in found.items() if NON_ADDITIVE_MARK not in path}
    for path, entry in found.items():
        if NON_ADDITIVE_MARK not in path:
            continue
        base = census.get(base_of(path))
        if base is not None:
            base["non_additive"] = path
            base["non_additive_frames"] = entry["frames"]
    return census


def census_folders(clips: Iterable[str]) -> list[str]:
    """One converter run per category keeps each dump a few tens of megabytes."""
    return sorted({CLIP_PREFIX + category(clip) + "/" for clip in clips})


def build_census(converter: Converter, package: Path, clips: Iterable[str], progress=None) -> dict:
    clips = list(clips)
    found: dict[str, dict] = {}
    folders = census_folders(clips)
    for number, folder in enumerate(folders, 1):
        if progress:
            progress(number, len(folders), folder)
        lines = converter.stream(["-i", str(package), "-f", folder, "-e", CLIP_EXTENSION, "-b", "DATA"], timeout=900)
        for path, entry in parse_clip_dump(lines):
            found[path] = entry
    census = fold_generated(found)
    for clip in clips:
        if clip not in census:
            census[clip] = {**_new_entry(), "missing": True}
    return {"version": CENSUS_VERSION, "clips": census}


def load_census(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == CENSUS_VERSION else None


def write_json(path: Path, data) -> None:
    """Write through a temporary file, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = proc.temp_name(path)
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    proc.replace_file(tmp, path)


def load_or_build_census(converter: Converter, package: Path, clips: Iterable[str], cache_file: Path, progress=None) -> dict:
    data = load_census(cache_file)
    if data is None:
        data = build_census(converter, package, clips, progress)
        write_json(cache_file, data)
    return data


def source_clip(info: dict) -> tuple[str, bool]:
    """Which clip to convert for this one, and whether the converter must compose it.

    An additive clip holds only the difference from another pose; played alone it
    folds every bone onto its parent. The game ships a ready '+non_additive' version
    of most of them; the rest are composed over the bind pose.
    """
    if not info.get("additive"):
        return "", False
    if info.get("non_additive"):
        return info["non_additive"], False
    return "", True


# --- Status -----------------------------------------------------------------


def status_entry(clip: str, info: dict, verdict: str = "unchecked", note: str = "") -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict '{verdict}'")
    return {
        "category": category(clip),
        "skeleton": info.get("skeleton", OTHER),
        "additive": bool(info.get("additive")),
        "frames": int(info.get("frames", 0)),
        "duration": round(float(info.get("duration", 0.0)), 4),
        "verdict": verdict,
        "note": note,
    }


def initial_verdict(info: dict) -> tuple[str, str]:
    """What is known without loading: clips for other skeletons never fit an agent."""
    if info.get("missing"):
        return "converter-error", "The converter could not read this clip."
    if info.get("skeleton") != WORLDMODEL:
        skeleton = info.get("skeleton_path", "").rsplit("/", 1)[-1] or "an unknown skeleton"
        return "other-skeleton", f"Made for {skeleton}, not the agent skeleton."
    return "unchecked", ""


def read_status(path: Path) -> dict | None:
    """clip_status.json, or None when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("version") == STATUS_VERSION and isinstance(data.get("clips"), dict):
        return data
    return None


def load_status(path: Path) -> dict:
    return read_status(path) or {"version": STATUS_VERSION, "clips": {}}


def build_status(census: dict, results: dict[str, dict], previous: dict | None = None) -> dict:
    """Every clip in the census, with the latest verdict where there is one.

    A result that kept its measurements is judged again, so a change to the rules
    applies without loading the clips again. Clips without a new result keep their
    verdict from `previous`, an earlier clip_status.json.
    """
    earlier = (previous or {}).get("clips", {})
    clips = {}
    for clip, info in sorted(census["clips"].items()):
        verdict, note = initial_verdict(info)
        result = results.get(clip)
        if verdict != "unchecked":
            pass
        elif result and result.get("metrics"):
            verdict, note = judge(info, result["metrics"], clip)
        elif result:
            verdict, note = result["verdict"], result.get("note", "")
        elif clip in earlier and earlier[clip].get("verdict") in VERDICTS:
            verdict, note = earlier[clip]["verdict"], earlier[clip].get("note", "")
        clips[clip] = status_entry(clip, info, verdict, note)
    return {"version": STATUS_VERSION, "clips": clips}


def judge(info: dict, metrics: dict, clip: str = "") -> tuple[str, str]:
    """Turn what the sweep measured on one clip into a verdict and a short reason."""
    if metrics.get("error"):
        return "converter-error", metrics["error"][:200]
    if metrics.get("timeout"):
        return "timeout", "Loading took too long."
    if not metrics.get("action"):
        return "no-animation", "The converter wrote no animation."
    if metrics.get("nan"):
        return "collapsed", "The pose has invalid (NaN) values."
    stretch = metrics.get("bone_stretch", 0.0)
    if stretch > BONE_LENGTH_LIMIT:
        if clip.startswith(CLIP_PREFIX + "viewmodel/"):
            return "collapsed", f"A first-person clip: it places the arms for the camera, pulling bones up to {stretch:.0%} out of place."
        return "collapsed", f"Bones change length by up to {stretch:.0%}."
    if metrics.get("scale_deviation", 0.0) > SCALE_TOLERANCE:
        return "collapsed", f"Bones are scaled by up to {metrics['scale_deviation']:.0%}."
    if metrics.get("pelvis_to_head", 1.0) < MIN_PELVIS_TO_HEAD:
        return "collapsed", f"The head comes within {metrics['pelvis_to_head']:.2f} m of the pelvis."
    expected = int(info.get("frames", 0))
    frames = int(metrics.get("frames", 0))
    if expected and abs(frames - expected) > FRAME_TOLERANCE:
        return "length-mismatch", f"{frames} frames loaded, the clip has {expected}."
    notes = []
    if metrics.get("composed"):
        notes.append("Additive: composed over the bind pose; in game it plays on top of another clip.")
    elif info.get("additive"):
        notes.append("Additive: loads the game's non-additive version.")
    if stretch > BONE_LENGTH_TOLERANCE:
        where = f" ({metrics['stretch_bone']})" if metrics.get("stretch_bone") else ""
        notes.append(f"The clip changes bone lengths by up to {stretch:.0%}{where}, as the game data does.")
    if metrics.get("hand_gap_change", 0.0) > HAND_GAP_NOTE:
        notes.append(f"The weapon leaves the right hand (moves {metrics['hand_gap_change']:.2f} m).")
    return ("additive" if metrics.get("composed") else "ok"), " ".join(notes)


def frames_at_clip_rate(info: dict, seconds: float) -> int:
    """How many game frames `seconds` of animation covers, at the clip's own frame rate."""
    frames, duration = int(info.get("frames", 0)), float(info.get("duration", 0.0))
    if frames < 2 or duration <= 0:
        return max(frames, 1)
    return round(seconds * (frames - 1) / duration) + 1


# --- Previews ---------------------------------------------------------------

PREVIEW_BONES = (
    "pelvis", "spine_0", "spine_1", "spine_2", "spine_3", "neck_0", "head_0",
    "clavicle_L", "arm_upper_L", "arm_lower_L", "hand_L",
    "clavicle_R", "arm_upper_R", "arm_lower_R", "hand_R",
    "leg_upper_L", "leg_lower_L", "ankle_L", "ball_L",
    "leg_upper_R", "leg_lower_R", "ankle_R", "ball_R",
    "wpn",
)
_SKIP_IN_PREVIEW = ("finger", "twist", "jiggle", "eye", "attach", "_aim", "camera", "empty", "helper", "ik_", "lip", "brow", "jaw")


def preview_name(clip: str) -> str:
    return hashlib.sha1(clip.encode("utf-8")).hexdigest()[:16] + ".json"


def side_of(bone: str) -> str:
    lower = bone.lower()
    if lower.endswith(("_l", ".l")) or "_l_" in lower or lower.startswith("l_"):
        return "L"
    if lower.endswith(("_r", ".r")) or "_r_" in lower or lower.startswith("r_"):
        return "R"
    return "C"


def preview_bones(parents: dict[str, str | None], limit: int = 30) -> list[str]:
    """The bones a stick figure needs: the agent's major bones, or for another skeleton
    every bone that is not a finger, twist, jiggle or helper, in hierarchy order."""
    if all(bone in parents for bone in PREVIEW_BONES[:-1]):
        return [bone for bone in PREVIEW_BONES if bone in parents]
    ordered = []
    children: dict[str | None, list[str]] = {}
    for bone, parent in parents.items():
        children.setdefault(parent, []).append(bone)
    stack = list(reversed(children.get(None, [])))
    while stack:
        bone = stack.pop()
        ordered.append(bone)
        stack.extend(reversed(children.get(bone, [])))
    keep = [bone for bone in ordered if not any(word in bone.lower() for word in _SKIP_IN_PREVIEW)]
    return keep[:limit]


def reduce_parents(bones: list[str], parents: dict[str, str | None]) -> list[int]:
    """Each kept bone's nearest kept ancestor, as an index into `bones` (-1 for none)."""
    index = {bone: i for i, bone in enumerate(bones)}
    result = []
    for bone in bones:
        parent = parents.get(bone)
        while parent is not None and parent not in index:
            parent = parents.get(parent)
        result.append(index.get(parent, -1) if parent is not None else -1)
    return result


def make_preview(clip: str, fps: float, bones: list[str], parents: list[int], frames: list[list[float]]) -> dict:
    if len(parents) != len(bones):
        raise ValueError("one parent per bone")
    for frame in frames:
        if len(frame) != 3 * len(bones):
            raise ValueError("each frame holds x, y, z for every bone")
    return {
        "version": PREVIEW_VERSION,
        "clip": clip,
        "fps": fps,
        "bones": list(bones),
        "parents": list(parents),
        "side": [side_of(bone) for bone in bones],
        "frames": [[round(v, 3) + 0.0 for v in frame] for frame in frames],
    }


def write_preview(preview_dir: Path, preview: dict) -> Path:
    path = preview_dir / preview_name(preview["clip"])
    write_json(path, preview)
    return path


def read_preview(preview_dir: Path, clip: str) -> dict | None:
    try:
        data = json.loads((Path(preview_dir) / preview_name(clip)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == PREVIEW_VERSION else None


def sample_times(duration: float, fps: float = PREVIEW_FPS) -> list[float]:
    """Seconds to sample a clip at, ending on its last frame."""
    if duration <= 0:
        return [0.0]
    count = max(1, math.ceil(duration * fps - 1e-6))
    return [min(duration, i / fps) for i in range(count)] + [duration]


# --- Choosing clips for a held weapon ---------------------------------------

_FAMILY_PREFIXES = ("knife_", "rifle_", "pistol_", "grenade_")


def folder_token(folder: str) -> str:
    """The weapon a clip folder is for: 'knife_karambit' -> 'karambit', '_default_knife' -> 'knife'."""
    name = folder.rsplit("/", 1)[-1]
    if name.startswith("_default_"):
        return name[len("_default_"):]
    for prefix in _FAMILY_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def weapon_folder(weapon_key: str, folders: Iterable[str]) -> str:
    """The clip folder made for one weapon, or '' when it uses its family's default clips.

    Keys and folders are named a little differently ('ak47' and 'rifle_ak',
    'knife_default_ct' and 'default_ct'), so an exact match wins, then the longest
    folder name the key starts with.
    """
    key = weapon_key.removeprefix("knife_")
    best = ""
    for folder in folders:
        name = folder.rsplit("/", 1)[-1]
        if name.startswith("_default_"):
            continue
        token = folder_token(folder)
        if token == key:
            return folder
        if key.startswith(token) and len(token) > len(folder_token(best) if best else ""):
            best = folder
    return best


def action_name(clip: str) -> str:
    """A clip's name with its weapon taken out: '.../knife_karambit/draw_crouch_karambit' -> 'draw_crouch'."""
    token = folder_token(folder_of(clip))
    stem = clip.rsplit("/", 1)[-1]
    parts = stem.split("_")
    token_parts = token.split("_")
    for i in range(len(parts) - len(token_parts), -1, -1):
        if parts[i:i + len(token_parts)] == token_parts:
            return "_".join(parts[:i] + parts[i + len(token_parts):])
    return stem


def clips_for_weapon(clips: list[str], family_folder: str, own_folder: str, shared_folder: str) -> list[str]:
    """A held weapon's own clips first, then its family's default clips that it does not
    replace with its own, then the clips shared by every weapon."""
    own = [c for c in clips if own_folder and folder_of(c) == own_folder]
    replaced = {action_name(c) for c in own}
    default = [c for c in clips if folder_of(c) == family_folder and action_name(c) not in replaced]
    shared = [c for c in clips if c.startswith(shared_folder + "/")]  # with its 'defuse' folder
    return own + default + shared
