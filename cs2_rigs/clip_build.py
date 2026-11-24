"""Load clips onto an agent, check each one and write its stick-figure preview. Runs inside a background Blender:

    blender --background --factory-startup --python clip_build.py -- <job.json>

The job file names the converter, the game package, the agent's rig export, the
clip census, the clips to check, a scratch folder, the preview folder and a results
file. Clips are converted a folder at a time into the scratch folder, which is
emptied after each folder. One JSON line per clip is appended to the results file,
so a run that stops part-way can be resumed.
"""

from __future__ import annotations

import importlib
import json
import math
import shutil
import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

_ADDON_DIR = Path(__file__).resolve().parent
if str(_ADDON_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_ADDON_DIR.parent))
_package = _ADDON_DIR.name
importer = importlib.import_module(f"{_package}.importer")
clipdata = importlib.import_module(f"{_package}.core.clipdata")
converter_mod = importlib.import_module(f"{_package}.core.converter")

GAME_FPS = 30
CHECK_SAMPLES = 5
# Bones whose length to their parent a clip never changes. The pelvis is left
# out: it moves away from the root motion bone in every clip.
_BODY = [bone for bone in clipdata.PREVIEW_BONES if bone not in ("pelvis", "wpn")]
_WEAPON_CATEGORIES = ("world/rifle", "world/pistol", "world/knife", "world/grenade", "world/equipment")


def _clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = GAME_FPS, 1.0


def load_rig(rig_glb: Path) -> bpy.types.Object:
    objects = importer._import_glb(bpy.context, rig_glb)
    armatures = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise RuntimeError(f"Expected one skeleton in {rig_glb.name}, found {len(armatures)}.")
    armature = armatures[0]
    if armature.animation_data and armature.animation_data.action:
        base = armature.animation_data.action
        armature.animation_data.action = None
        bpy.data.actions.remove(base)
    for bone in armature.pose.bones:
        bone.matrix_basis.identity()
    # Meshes are not needed to sample bones, and skinning them slows every frame change.
    bpy.data.batch_remove([obj for obj in objects if obj.type == "MESH"])
    return armature


def facing_correction(armature: bpy.types.Object) -> Matrix:
    """A turn about Z that makes the agent's rest pose face -Y, the way previews are drawn."""
    bones = armature.data.bones
    forward = Vector((0.0, 0.0, 0.0))
    for side in ("L", "R"):
        if f"ankle_{side}" in bones and f"ball_{side}" in bones:
            forward += bones[f"ball_{side}"].head_local - bones[f"ankle_{side}"].head_local
    forward = (armature.matrix_world.to_3x3() @ forward).to_2d()
    if forward.length < 1e-6:
        return Matrix.Identity(4)
    angle = math.atan2(forward.y, forward.x) - math.atan2(-1.0, 0.0)
    return Matrix.Rotation(-angle, 4, "Z")


def _assign(armature: bpy.types.Object, action: bpy.types.Action) -> None:
    importer._assign_action(armature, action)


def _set_time(scene: bpy.types.Scene, start: float, seconds: float) -> None:
    frame = start + seconds * GAME_FPS
    scene.frame_set(int(math.floor(frame)), subframe=frame - math.floor(frame))


def sample_clip(armature, action, info: dict, clip: str, preview: dict, correction: Matrix) -> dict:
    """Play the action on the agent and measure it; fill `preview` with sampled bone positions."""
    scene = bpy.context.scene
    _assign(armature, action)
    start, end = action.frame_range
    duration = max(0.0, (end - start) / GAME_FPS)
    pose = armature.pose.bones
    world = correction @ armature.matrix_world
    rest = {bone.name: bone.head_local.copy() for bone in armature.data.bones}
    pairs = [(bone.parent.name, bone.name) for bone in armature.data.bones if bone.name in _BODY and bone.parent]
    pairs = [(a, b) for a, b in pairs if (rest[a] - rest[b]).length > 0.01]

    metrics = {"action": True, "frames": clipdata.frames_at_clip_rate(info, duration), "seconds": round(duration, 4)}
    stretch = scale_dev = 0.0
    stretch_bone = ""
    gaps = []
    pelvis_to_head = math.inf
    nan = False
    first: dict[str, Vector] = {}
    motion = 0.0
    checks = [duration * i / (CHECK_SAMPLES - 1) for i in range(CHECK_SAMPLES)]
    for seconds in checks:
        _set_time(scene, start, seconds)
        for parent, child in pairs:
            length = (pose[child].head - pose[parent].head).length
            if math.isnan(length):
                nan = True
                continue
            change = abs(length / (rest[child] - rest[parent]).length - 1.0)
            if change > stretch:
                stretch, stretch_bone = change, child
        for name in _BODY:
            if name in pose:
                scale = pose[name].matrix.to_scale()
                if any(math.isnan(v) for v in scale):
                    nan = True
                else:
                    scale_dev = max(scale_dev, max(abs(v - 1.0) for v in scale))
        if "pelvis" in pose and "head_0" in pose:
            pelvis_to_head = min(pelvis_to_head, (pose["head_0"].head - pose["pelvis"].head).length)
        if "wpn" in pose and "hand_R" in pose:
            gaps.append((pose["wpn"].head - pose["hand_R"].head).length)
        for name in ("pelvis", "head_0", "hand_L", "hand_R", "ankle_L", "ankle_R"):
            if name in pose:
                where = pose[name].head.copy()
                first.setdefault(name, where)
                motion = max(motion, (where - first[name]).length)
    metrics.update(
        nan=nan, bone_stretch=round(stretch, 4), stretch_bone=stretch_bone, scale_deviation=round(scale_dev, 4),
        pelvis_to_head=round(pelvis_to_head if pelvis_to_head != math.inf else 0.0, 4), motion=round(motion, 4),
    )
    if clipdata.category(clip) in _WEAPON_CATEGORIES:
        metrics["hand_gap"] = round(max(gaps, default=0.0), 4)
        metrics["hand_gap_change"] = round(max(gaps, default=0.0) - min(gaps, default=0.0), 4)

    names = preview["bones"]
    frames = []
    for seconds in clipdata.sample_times(duration, preview["fps"]):
        _set_time(scene, start, seconds)
        row = []
        for name in names:
            point = world @ pose[name].head
            row.extend(0.0 if math.isnan(v) else v for v in point)
        frames.append(row)
    preview["frames"] = frames
    return metrics


def _remove_action(armature, action) -> None:
    if armature.animation_data:
        armature.animation_data.action = None
    bpy.data.actions.remove(action)


def run(job: dict) -> None:
    _clear_scene()
    conv = converter_mod.Converter(Path(job["cli"]))
    package = Path(job["package"])
    rig_glb = Path(job["rig"])
    census = clipdata.load_census(Path(job["census"]))["clips"]
    scratch = Path(job["scratch"])
    preview_dir = Path(job["preview_dir"])
    results = Path(job["results"])
    write_previews = job.get("previews", True)

    scratch.mkdir(parents=True, exist_ok=True)
    armature = load_rig(rig_glb)
    parents = {bone.name: bone.parent.name if bone.parent else None for bone in armature.data.bones}
    bones = clipdata.preview_bones(parents)
    bone_parents = clipdata.reduce_parents(bones, parents)
    correction = facing_correction(armature)

    folders: dict[str, list[str]] = {}
    for clip in job["clips"]:
        folders.setdefault(clipdata.folder_of(clip), []).append(clip)

    with open(results, "a", encoding="utf-8") as out:
        for folder, clips in folders.items():
            exports = scratch / "export"
            shutil.rmtree(exports, ignore_errors=True)
            started = time.monotonic()
            folder_error = ""
            try:
                conv.export_clip_folder(package, folder, exports)
            except converter_mod.ConverterError as exc:
                folder_error = str(exc)
            print(f"CLIPS {folder}: {len(clips)} clips converted in {time.monotonic() - started:.1f} s", flush=True)
            for clip in clips:
                info = census.get(clip, {})
                metrics: dict = {}
                clip_started = time.monotonic()
                try:
                    if folder_error:
                        raise converter_mod.ConverterError(folder_error)
                    source, compose = clipdata.source_clip(info)
                    clip_glb = converter_mod.exported_path(exports, (source or clip) + ".vnmclip_c")
                    if compose:
                        clip_glb = conv.export_clip(package, clip, scratch / "composed", compose_additive=True)
                    if not clip_glb.is_file():
                        raise converter_mod.ConverterError(f"The converter wrote no file for this clip ({clip_glb.name}).")
                    try:
                        action = importer.import_clip_action(bpy.context, rig_glb, clip_glb, clip, scratch)
                    except RuntimeError as exc:
                        if "no animation" not in str(exc):
                            raise
                        metrics = {"action": False}
                    else:
                        preview = {"fps": clipdata.PREVIEW_FPS, "bones": bones}
                        metrics = sample_clip(armature, action, info, clip, preview, correction)
                        metrics["composed"] = compose
                        _remove_action(armature, action)
                        if write_previews:
                            clipdata.write_preview(
                                preview_dir, clipdata.make_preview(clip, preview["fps"], bones, bone_parents, preview["frames"])
                            )
                except Exception as exc:  # one bad clip must not stop the run
                    metrics = {"error": str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__}
                verdict, note = clipdata.judge(info, metrics, clip)
                record = {"clip": clip, "verdict": verdict, "note": note, "metrics": metrics,
                          "seconds": round(time.monotonic() - clip_started, 2)}
                out.write(json.dumps(record) + "\n")
                out.flush()
                print(f"CLIP {verdict:16} {clip}", flush=True)
            shutil.rmtree(exports, ignore_errors=True)
            shutil.rmtree(scratch / "composed", ignore_errors=True)
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_recursive=True)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:]
    run(json.loads(Path(arguments[0]).read_text(encoding="utf-8")))
    print("CLIPS DONE", flush=True)
