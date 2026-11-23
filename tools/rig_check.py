"""Check the IK controls against a game clip in a real Blender.

    blender --background --factory-startup --python tools/rig_check.py -- <out_dir>

For every limb, at several frames of a run clip: switching to IK must not move the
limb, moving the IK control must move the hand/foot, and switching back to FK must
keep that new pose.
"""

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import rig  # noqa: E402

out_dir = Path(sys.argv[sys.argv.index("--") + 1])
out_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
wm = bpy.context.window_manager
context = bpy.context
scene = context.scene

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object
wm.cs2rig_clip = "world/rifle/_default_rifle/run_n_rifle"
assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}
bones_before = len(armature.data.bones)
assert bpy.ops.cs2rig.build_rig() == {"FINISHED"}
limbs = rig.rigged_limbs(armature)
print("RIG limbs:", limbs, "bones:", bones_before, "->", len(armature.data.bones))
assert len(limbs) == 4


def joints(limb, side):
    context.view_layer.update()
    names = rig.limb_bones(limb, side)
    pb = armature.pose.bones
    return [pb[names[k]].head.copy() for k in ("upper", "lower", "end")], pb[names["end"]].matrix.copy()


def rotation_error(m1, m2):
    return m1.to_quaternion().rotation_difference(m2.to_quaternion()).angle


# 1. With the switch at 0 the rig must not change the clip at all.
action = armature.animation_data.action
start, end = (int(v) for v in action.frame_range)
frames = sorted({start, (start * 2 + end) // 3, (start + end) // 2, (start + end * 2) // 3, end})
worst_switch = worst_back = worst_rot = 0.0
for frame in frames:
    for limb, side in limbs:
        scene.frame_set(frame)  # resets unkeyed control changes from the last limb
        armature[rig.switch_prop(limb, side)] = 0.0
        fk_points, fk_end = joints(limb, side)

        rig.snap_ik_to_fk(context, armature, limb, side)
        ik_points, ik_end = joints(limb, side)
        error = max((p - q).length for p, q in zip(fk_points, ik_points))
        worst_switch = max(worst_switch, error)
        worst_rot = max(worst_rot, rotation_error(fk_end, ik_end))

        # 2. Moving the control moves the hand/foot by the same amount.
        names = rig.limb_bones(limb, side)
        control = armature.pose.bones[names["ik"]]
        moved = control.matrix.copy()
        offset = Vector((0.0, -0.05, 0.05))
        moved.translation += offset
        control.matrix = moved
        moved_points, _ = joints(limb, side)
        follow = (moved_points[2] - (ik_points[2] + offset)).length
        assert follow < 0.002, f"{limb} {side} frame {frame}: end did not follow its control ({follow:.4f} m)"
        upper_len = (moved_points[1] - moved_points[0]).length
        assert abs(upper_len - (ik_points[1] - ik_points[0]).length) < 0.002, "upper bone changed length"

        # 3. Back to FK keeps the edited pose.
        rig.snap_fk_to_ik(context, armature, limb, side)
        back_points, _ = joints(limb, side)
        worst_back = max(worst_back, max((p - q).length for p, q in zip(moved_points, back_points)))
        assert armature[rig.switch_prop(limb, side)] == 0.0

# Only the controls that drive a limb are shown, and every bone is in one collection.
names = rig.limb_bones("arm", "L")
pb = armature.pose.bones
for value, ik_hidden in ((0.0, True), (1.0, False)):
    armature[rig.switch_prop("arm", "L")] = value
    armature.update_tag()
    scene.frame_set(scene.frame_current)
    context.view_layer.update()
    assert pb[names["ik"]].hide == ik_hidden and pb[names["pole"]].hide == ik_hidden, f"IK control visibility at {value}"
    assert pb["arm_lower_L_TWIST"].hide is False or not pb["arm_lower_L_TWIST"].bone.collections[0].is_visible
    assert pb[names["upper"]].hide != ik_hidden and pb[names["end"]].hide != ik_hidden, f"FK ring visibility at {value}"
armature[rig.switch_prop("arm", "L")] = 0.0
loose = [b.name for b in armature.data.bones if len(b.collections) != 1]
assert not loose, f"bones not in exactly one collection: {loose[:5]}"
shown = {c.name: len(c.bones) for c in armature.data.collections_all if c.is_visible}
shaped = [b.name for b in pb if b.custom_shape]
unshaped_visible = [b.name for b in pb if not b.custom_shape and b.bone.collections[0].is_visible]
print("RIG visible collections:", shown)
print("RIG bones with shapes:", len(shaped), "visible without a shape:", unshaped_visible)
assert not unshaped_visible
flat = pb[rig.rig_layout.ROOT_DISPLAY_BONE]
tilt = (armature.matrix_world @ flat.matrix).to_3x3().col[2].angle(Vector((0, 0, 1)))
assert pb["root_motion"].custom_shape_transform == flat and tilt < 0.01, "the root ring is not flat"


def local_twist(bone_name, axis):
    """How far a bone is turned about one of its own rest axes, in degrees."""
    context.view_layer.update()
    bone = pb[bone_name]
    parent_rest = bone.bone.parent.matrix_local.inverted() @ bone.bone.matrix_local
    local = parent_rest.inverted() @ bone.parent.matrix.inverted() @ bone.matrix
    return math.degrees(local.to_quaternion().to_swing_twist(axis)[1])


# Twist bones share the hand's twist and undo part of the upper arm's.
scene.frame_set(start)
for side in "LR":
    for name in (f"hand_{side}", f"arm_upper_{side}"):
        pb[name].matrix_basis = pb[name].matrix_basis @ Matrix.Rotation(math.radians(60), 4, "X")
    hand = local_twist(f"hand_{side}", "X")
    upper = local_twist(f"arm_upper_{side}", "X")
    shares = {n: local_twist(n, "X") for n in (f"arm_lower_{side}_TWIST", f"arm_lower_{side}_TWIST1", f"arm_upper_{side}_TWIST", f"arm_upper_{side}_TWIST1")}
    print(f"RIG twist {side}: hand {hand:.1f} upper {upper:.1f} ->", {k: round(v, 1) for k, v in shares.items()})
    for (name, value), (source, share) in zip(shares.items(), ((hand, 0.4), (hand, 0.8), (upper, -2 / 3), (upper, -1 / 3))):
        assert abs(value - source * share) < 2.0, f"{name} turned {value:.1f}, expected {source * share:.1f}"
scene.frame_set(start)

# Foot roll: one way pivots on the heel (toes rise), the other on the ball (heel rises, toes stay).
for side in "LR":
    names = rig.limb_bones("leg", side)
    rig.snap_ik_to_fk(context, armature, "leg", side)
    context.view_layer.update()
    ankle0, toe0 = pb[names["end"]].head.copy(), pb[names["toe"]].head.copy()
    control = pb[names["ik"]]
    foot_up = control.matrix.to_3x3() @ control.bone.matrix_local.to_3x3().inverted() @ Vector((0, 0, 1))
    toe_tip0 = pb[names["toe"]].matrix.copy()
    roll = pb[names["roll"]]
    roll.rotation_euler = (math.radians(-40), 0, 0)
    context.view_layer.update()
    lifted = (pb[names["end"]].head - ankle0).dot(foot_up)
    toe_moved = (pb[names["toe"]].head - toe0).length
    toe_turned = pb[names["toe"]].matrix.to_quaternion().rotation_difference(toe_tip0.to_quaternion()).angle
    assert lifted > 0.03 and toe_moved < 0.002 and toe_turned < 0.01, f"ball roll {side}: ankle {lifted:.3f}, toe moved {toe_moved:.4f}, turned {toe_turned:.3f}"
    roll.rotation_euler = (math.radians(30), 0, 0)
    context.view_layer.update()
    toe_lift = (pb[names["toe"]].head - toe0).dot(foot_up)
    assert toe_lift > 0.02 and abs((pb[names["end"]].head - ankle0).dot(foot_up)) < toe_lift, f"heel roll {side}: toe {toe_lift:.3f}"
    print(f"RIG foot roll {side}: heel lifts {lifted * 100:.1f} cm on the ball, toes lift {toe_lift * 100:.1f} cm on the heel")
    roll.rotation_euler = (0, 0, 0)
    rig.snap_fk_to_ik(context, armature, "leg", side)
scene.frame_set(start)

# Follow switches: turning one on or off moves nothing, and while on the follower goes with its leader.
def follow_shift(prop, on):
    owner = pb[rig.FOLLOWS[prop][0]]
    before = owner.matrix.copy()
    assert bpy.ops.cs2rig.set_follow(prop=prop, on=on) == {"FINISHED"}
    context.view_layer.update()
    return (owner.matrix.translation - before.translation).length + owner.matrix.to_quaternion().rotation_difference(before.to_quaternion()).angle


assert rig.follows(armature) == list(rig.FOLLOWS)
for prop, (owner_name, leader_name, _) in rig.FOLLOWS.items():
    scene.frame_set(start)
    if owner_name.startswith("IK-"):
        rig.snap_ik_to_fk(context, armature, "arm", owner_name[-1])  # a hand control is stale until its arm is under IK
    on_shift = follow_shift(prop, True)
    leader, owner = pb[leader_name], pb[owner_name]
    gap = leader.matrix.inverted() @ owner.matrix
    leader.matrix = leader.matrix @ Matrix.Rotation(math.radians(35), 4, "Z") @ Matrix.Translation((0.05, 0.02, 0.0))
    context.view_layer.update()
    carried = (leader.matrix.inverted() @ owner.matrix).translation - gap.translation
    off_shift = follow_shift(prop, False)
    print(f"RIG follow {prop}: shift on {on_shift:.5f}, off {off_shift:.5f}, slip while following {carried.length * 1000:.2f} mm")
    assert on_shift < 1e-3 and off_shift < 1e-3 and carried.length < 1e-3
    if owner_name.startswith("IK-"):
        rig.snap_fk_to_ik(context, armature, "arm", owner_name[-1])
scene.frame_set(start)

print("RIG frames checked:", frames)
print("RIG worst joint shift when switching FK->IK (mm):", round(worst_switch * 1000, 2))
print("RIG worst hand/foot rotation change FK->IK (deg):", round(worst_rot * 57.2958, 3))
print("RIG worst joint shift when switching IK->FK (mm):", round(worst_back * 1000, 2))
assert worst_switch < 0.005 and worst_back < 0.002 and worst_rot < 0.01

# Picture: left arm and right leg pulled away from the clip pose with IK.
scene.frame_set((start + end) // 2)
for limb, side, offset in (("arm", "L", Vector((0.15, -0.1, 0.35))), ("leg", "R", Vector((-0.1, -0.35, 0.3)))):
    rig.snap_ik_to_fk(context, armature, limb, side)
    control = armature.pose.bones[rig.limb_bones(limb, side)["ik"]]
    moved = control.matrix.copy()
    moved.translation += offset
    control.matrix = moved
    control.keyframe_insert("location")
    armature.keyframe_insert(f'["{rig.switch_prop(limb, side)}"]')
context.view_layer.update()

visible = [o for o in armature.children_recursive if o.type == "MESH" and not o.hide_render]
camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(camera)
centre = Vector((0.0, 0.0, 1.0))
camera.location = centre + Vector((2.2, -3.0, 0.3))
camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = camera
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 4
sun.rotation_euler = (0.9, 0.2, 0.6)
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
world.color = (0.25, 0.27, 0.3)
scene.world = world
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 900, 1100
scene.render.filepath = str(out_dir / "rig.png")
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / "rig.blend"))

# Removing the controls gives back the imported skeleton, and they can be built again.
bpy.ops.object.mode_set(mode="OBJECT")
assert bpy.ops.cs2rig.remove_rig() == {"FINISHED"}
assert len(armature.data.bones) == bones_before, "removing the controls left bones behind"
assert not any(b.custom_shape for b in armature.pose.bones) and not rig.has_rig(armature)
assert not [d.data_path for d in armature.animation_data.drivers], "removing the controls left drivers behind"
assert bpy.ops.cs2rig.build_rig() == {"FINISHED"} and len(rig.rigged_limbs(armature)) == 4
print("RIG OK")
