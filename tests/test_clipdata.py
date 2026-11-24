import json
import struct

import pytest

from core import clipdata, glb

ADDITIVE = "animation/anims/world/equipment/c4/idle_c4"
DRAW = "animation/anims/world/knife/_default_knife/draw_knife"

DUMP = f"""\
[1/3] {ADDITIVE}.vnmclip_c
\tResource Type: NmClip [Version 0] [Header Version: 12]

--- Resource External Refs: ---
\tId:               Resource Name:
\tA73DECAF37FBCAF3  {ADDITIVE}.vnmclip+non_additive.vnmclip
\tA3193FB3BC4AD00A  animation/skeletons/characters/worldmodel.vnmskel

--- Data for block "DATA" ---
<!-- kv3 encoding:text:version{{e21c7f3c}} -->
{{
\tm_skeleton = resource:"animation/skeletons/characters/worldmodel.vnmskel"
\tm_nNumFrames = 2
\tm_flDuration = 0.0333
\tm_compressedPoseData = #[  ]
\tm_trackCompressionSettings =
\t[
\t\t{{
\t\t\tm_skeleton = resource:"not/a/secondary.vnmskel"
\t\t}},
\t]
\tm_secondaryAnimations = [  ]
\tm_rootMotion =
\t{{
\t\tm_flAverageLinearVelocity = 0.0
\t}}
\tm_bIsAdditive = true
}}
[2/3] {ADDITIVE}.vnmclip+non_additive.vnmclip_c
--- Data for block "DATA" ---
{{
\tm_skeleton = resource:"animation/skeletons/characters/worldmodel.vnmskel"
\tm_nNumFrames = 2
\tm_flDuration = 0.0333
\tm_bIsAdditive = false
}}
[3/3] {DRAW}.vnmclip_c
--- Data for block "DATA" ---
{{
\tm_skeleton = resource:"animation/skeletons/characters/worldmodel.vnmskel"
\tm_nNumFrames = 31
\tm_flDuration = 1.0
\tm_secondaryAnimations =
\t[
\t\t{{
\t\t\tm_skeleton = resource:"animation/skeletons/weapons/knife_default_ct.vnmskel"
\t\t\tm_nNumFrames = 31
\t\t\tm_bIsAdditive = false
\t\t}},
\t]
\tm_rootMotion =
\t{{
\t\tm_flAverageLinearVelocity = 1.25
\t}}
\tm_bIsAdditive = false
}}
"""


def test_census_reads_the_fields_it_needs_line_by_line():
    found = dict(clipdata.parse_clip_dump(iter(DUMP.splitlines(keepends=True))))
    assert set(found) == {ADDITIVE, ADDITIVE + ".vnmclip+non_additive", DRAW}
    c4 = found[ADDITIVE]
    assert (c4["skeleton"], c4["frames"], c4["duration"], c4["additive"]) == ("worldmodel", 2, 0.0333, True)
    assert c4["non_additive"] == ADDITIVE + ".vnmclip+non_additive"
    assert c4["secondary"] == []  # a nested m_skeleton outside m_secondaryAnimations is not one
    draw = found[DRAW]
    assert draw["secondary"] == ["animation/skeletons/weapons/knife_default_ct.vnmskel"]
    assert draw["root_speed"] == 1.25 and not draw["additive"]


def test_census_folds_generated_clips_into_their_own():
    census = clipdata.fold_generated(dict(clipdata.parse_clip_dump(DUMP.splitlines())))
    assert set(census) == {ADDITIVE, DRAW}
    assert census[ADDITIVE]["non_additive_frames"] == 2
    assert clipdata.source_clip(census[ADDITIVE]) == (ADDITIVE + ".vnmclip+non_additive", False)
    assert clipdata.source_clip(census[DRAW]) == ("", False)
    assert clipdata.source_clip({"additive": True, "non_additive": ""}) == ("", True)


def test_census_runs_one_dump_per_category():
    class FakeConverter:
        def __init__(self):
            self.calls = []

        def stream(self, args, timeout):
            self.calls.append(args[args.index("-f") + 1])
            return iter(DUMP.splitlines()) if "equipment" in self.calls[-1] else iter(())

    fake = FakeConverter()
    data = clipdata.build_census(fake, "pak01_dir.vpk", [ADDITIVE, DRAW, "animation/anims/world/knife/knife_m9/draw_m9"])
    assert fake.calls == ["animation/anims/world/equipment/", "animation/anims/world/knife/"]
    assert data["clips"]["animation/anims/world/knife/knife_m9/draw_m9"]["missing"]
    assert data["clips"][DRAW]["frames"] == 31


def test_skeletons_and_categories():
    assert clipdata.skeleton_kind("animation/skeletons/characters/viewmodel.vnmskel") == "viewmodel"
    assert clipdata.skeleton_kind("animation/skeletons/characters/chicken/chicken.vnmskel") == "chicken"
    assert clipdata.skeleton_kind("animation/skeletons/egg/pristine_egg_world.vnmskel") == "other"
    assert clipdata.category(DRAW) == "world/knife"
    assert clipdata.category("animation/anims/egg/chick_egg_reaction01") == "egg"


def test_status_marks_other_skeletons_without_loading_them():
    census = {"clips": {
        DRAW: {"skeleton": "worldmodel", "frames": 31, "duration": 1.0},
        "animation/anims/viewmodel/knife/draw": {"skeleton": "viewmodel", "skeleton_path": "x/viewmodel.vnmskel", "frames": 5},
    }}
    status = clipdata.build_status(census, {DRAW: {"verdict": "ok", "note": ""}})
    assert status["version"] == 1
    assert status["clips"][DRAW]["verdict"] == "ok"
    assert status["clips"][DRAW]["category"] == "world/knife"
    viewmodel = status["clips"]["animation/anims/viewmodel/knife/draw"]
    assert viewmodel["verdict"] == "other-skeleton" and "viewmodel.vnmskel" in viewmodel["note"]
    with pytest.raises(ValueError):
        clipdata.status_entry(DRAW, {}, "fine")


def test_status_judges_stored_measurements_again_and_keeps_earlier_verdicts():
    other = "animation/anims/world/knife/_default_knife/idle_knife"
    census = {"clips": {DRAW: {"skeleton": "worldmodel", "frames": 31, "duration": 1.0},
                        other: {"skeleton": "worldmodel", "frames": 5, "duration": 0.1667}}}
    measured = {"action": True, "frames": 31, "bone_stretch": 0.1, "pelvis_to_head": 0.69}
    results = {DRAW: {"verdict": "collapsed", "note": "old rule", "metrics": measured}}
    previous = {"clips": {other: {"verdict": "timeout", "note": "slow"}}}
    status = clipdata.build_status(census, results, previous)["clips"]
    assert status[DRAW]["verdict"] == "ok"
    assert (status[other]["verdict"], status[other]["note"]) == ("timeout", "slow")
    crashed = {DRAW: {"verdict": "converter-error", "note": "Blender stopped", "metrics": {}}}
    assert clipdata.build_status(census, crashed)["clips"][DRAW]["verdict"] == "converter-error"


def test_judge_catches_a_collapsed_pose_and_a_short_clip():
    info = {"frames": 31, "duration": 1.0}
    good = {"action": True, "frames": 31, "bone_stretch": 0.001, "pelvis_to_head": 0.69}
    assert clipdata.judge(info, good) == ("ok", "")
    assert clipdata.judge(info, {**good, "frames": 30})[0] == "ok"
    assert clipdata.judge(info, {**good, "frames": 20})[0] == "length-mismatch"
    plate = {**good, "bone_stretch": 1.0, "pelvis_to_head": 0.0}
    assert clipdata.judge(info, plate)[0] == "collapsed"
    assert clipdata.judge(info, {**good, "pelvis_to_head": 0.1})[0] == "collapsed"
    assert clipdata.judge(info, {"action": False}) == ("no-animation", "The converter wrote no animation.")
    assert clipdata.judge(info, {"error": "boom"})[0] == "converter-error"
    assert clipdata.judge({**info, "additive": True}, {**good, "composed": True})[0] == "additive"
    assert "non-additive" in clipdata.judge({**info, "additive": True}, good)[1]
    assert "leaves the right hand" in clipdata.judge(info, {**good, "hand_gap": 0.6, "hand_gap_change": 0.4})[1]
    assert clipdata.judge(info, {**good, "hand_gap": 0.6, "hand_gap_change": 0.01}) == ("ok", "")
    proportions = clipdata.judge(info, {**good, "bone_stretch": 0.1, "stretch_bone": "head_0"})
    assert proportions[0] == "ok" and "10% (head_0)" in proportions[1]
    arms = clipdata.judge(info, {**good, "bone_stretch": 1.5}, "animation/anims/viewmodel/chicken/chick_arms_draw")
    assert arms[0] == "collapsed" and "first-person" in arms[1]
    assert clipdata.frames_at_clip_rate(info, 1.0) == 31
    assert clipdata.frames_at_clip_rate({"frames": 2, "duration": 0.0333}, 0.0333) == 2


def test_preview_keeps_major_bones_and_their_nearest_kept_parent(tmp_path):
    parents = {"root_motion": None, "pelvis": "root_motion", "spine_0": "pelvis", "spine_0_TWIST": "spine_0",
               "head_0": "spine_0_TWIST", "finger_0_L": "spine_0", "arm_upper_L": "spine_0"}
    bones = clipdata.preview_bones(parents)
    assert bones == ["root_motion", "pelvis", "spine_0", "head_0", "arm_upper_L"]
    assert clipdata.reduce_parents(bones, parents) == [-1, 0, 1, 2, 2]
    preview = clipdata.make_preview(DRAW, 15, bones, [-1, 0, 1, 2, 2], [[0.12345] * 15])
    assert preview["side"] == ["C", "C", "C", "C", "L"]
    assert preview["frames"][0][0] == 0.123
    path = clipdata.write_preview(tmp_path, preview)
    assert path.name == clipdata.preview_name(DRAW) and len(path.stem) == 16
    assert clipdata.read_preview(tmp_path, DRAW) == preview
    assert clipdata.read_preview(tmp_path, ADDITIVE) is None
    with pytest.raises(ValueError):
        clipdata.make_preview(DRAW, 15, bones, [-1], [])
    assert clipdata.sample_times(0.2, 15) == [0.0, 1 / 15, 2 / 15, 0.2]


def test_held_weapon_prefers_its_own_clip_folder():
    folders = ["animation/anims/world/knife/_default_knife", "animation/anims/world/knife/knife_karambit",
               "animation/anims/world/knife/default_ct", "animation/anims/world/rifle/rifle_ak",
               "animation/anims/world/rifle/rifle_m4a1_silencer", "animation/anims/world/rifle/rifle_m4a4",
               "animation/anims/world/pistol/pistol_usp", "animation/anims/world/knife/knife_m9"]
    assert clipdata.weapon_folder("knife_karambit", folders).endswith("knife_karambit")
    assert clipdata.weapon_folder("knife_default_ct", folders).endswith("default_ct")
    assert clipdata.weapon_folder("ak47", folders).endswith("rifle_ak")
    assert clipdata.weapon_folder("m4a4", folders).endswith("rifle_m4a4")
    assert clipdata.weapon_folder("m4a1_silencer", folders).endswith("rifle_m4a1_silencer")
    assert clipdata.weapon_folder("usp_silencer", folders).endswith("pistol_usp")
    assert clipdata.weapon_folder("knife_m9", folders).endswith("knife_m9")
    assert clipdata.weapon_folder("knife_flip", folders) == ""

    assert clipdata.action_name("animation/anims/world/knife/knife_karambit/draw_crouch_karambit") == "draw_crouch"
    assert clipdata.action_name(DRAW) == "draw"
    assert clipdata.action_name("animation/anims/world/rifle/rifle_ak/reload_ak_silent") == "reload_silent"

    base = "animation/anims/world/knife/"
    clips = [DRAW, base + "_default_knife/idle_knife", base + "knife_karambit/draw_karambit",
             base + "knife_m9/draw_m9", "animation/anims/world/shared/flinch_head", "animation/anims/world/shared/defuse/defuse_loop"]
    chosen = clipdata.clips_for_weapon(clips, base + "_default_knife", base + "knife_karambit", "animation/anims/world/shared")
    assert chosen == [base + "knife_karambit/draw_karambit", base + "_default_knife/idle_knife",
                      "animation/anims/world/shared/flinch_head", "animation/anims/world/shared/defuse/defuse_loop"]


def _glb(path, nodes, animation=None, floats=()):
    data = struct.pack(f"<{len(floats)}f", *floats)
    document = {"asset": {"version": "2.0"}, "nodes": nodes, "buffers": [{"byteLength": len(data)}], "bufferViews": [], "accessors": []}
    if animation:
        offset = 0
        for count, kind in animation["accessors"]:
            width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[kind]
            document["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": 4 * width * count})
            document["accessors"].append({"bufferView": len(document["bufferViews"]) - 1, "componentType": 5126, "count": count, "type": kind})
            offset += 4 * width * count
        document["animations"] = [animation["animation"]]
    glb.write_glb(path, document, data)


def test_graft_moves_tracks_onto_the_rig_by_bone_name(tmp_path):
    rig, clip, out = tmp_path / "rig.glb", tmp_path / "clip.glb", tmp_path / "out.glb"
    _glb(rig, [{"name": "agent", "children": [1]}, {"name": "pelvis", "children": [2]}, {"name": "head_0"}])
    times = [0.0, 1.0]
    rotations = [0, 0, 0, 1, 0, 0, 0, -1]  # the second key is the same rotation, flipped
    moves = [0, 1, 0, 0, 2, 0]
    _glb(clip, [{"name": "worldmodel", "children": [1, 2]}, {"name": "head_0"}, {"name": "camera"}], {
        "accessors": [(2, "SCALAR"), (2, "VEC4"), (2, "VEC3")],
        "animation": {"name": "anim\\clip", "samplers": [{"input": 0, "output": 1}, {"input": 0, "output": 2}],
                      "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}},
                                   {"sampler": 1, "target": {"node": 2, "path": "translation"}}]},
    }, times + rotations + moves)
    assert glb.graft_animation(rig, clip, out, "world/clip") == 1
    document, binary = glb.read_glb(out)
    assert glb.animation_names(document) == ["world/clip"]
    (animation,) = document["animations"]
    assert animation["channels"] == [{"sampler": 0, "target": {"node": 2, "path": "rotation"}}]
    keys = glb.read_floats(document, binary, animation["samplers"][0]["output"])
    assert keys == [(0, 0, 0, 1), (0, 0, 0, 1)]
    assert glb.read_floats(document, binary, animation["samplers"][0]["input"]) == [(0.0,), (1.0,)]
    assert json.dumps(document)  # still plain JSON
