"""Animation controls added on top of the game skeleton.

The game bones stay untouched so game clips keep playing on them (FK). Each limb
gets an IK chain built from separate mechanism bones, because the imported bones'
tails and axes are importer guesses and cannot be used for IK directly:

    MCH-<upper> -> MCH-<lower>   clean two-bone chain, solved by IK
    TGT-<bone>                   exact rest copy of a game bone, child of the mechanism
    IK-<end>, IK-<joint>         the hand/foot control and the elbow/knee pole
    IK-roll, IK-toe (legs)       foot roll about the heel and the ball, and the toes

A game bone follows its target through Copy Transforms, with the influence driven
by a 0..1 property on the armature object (0 = FK, 1 = IK).

Twist bones are separate: the game turns them itself and clips carry nothing for
them, so they get constraints that are always on.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix, Vector

from . import rig_layout

RIG_VERSION = 3
RIG_VERSION_PROP = "cs2rig_rig_version"
CONSTRAINT_PREFIX = "CS2 Rigs"
FOLLOW_CONSTRAINT = f"{CONSTRAINT_PREFIX} IK"
TWIST_CONSTRAINT = f"{CONSTRAINT_PREFIX} Twist"
ROLL_CONSTRAINT = f"{CONSTRAINT_PREFIX} Roll"
RIG_BONE_PREFIXES = ("MCH-", "TGT-", "IK-")
PARENT_CONSTRAINT = f"{CONSTRAINT_PREFIX} Follow"
ROOT_BONE = "root_motion"
# switch -> (bone that follows, bone it follows, label). The right hand carries the
# weapon, so only the left hand can be pinned to it; both ways round would be a loop.
FOLLOWS = {
    "weapon_in_hand": ("wpn", "hand_R", "Weapon Follows Right Hand"),
    "hand_L_on_weapon": ("IK-hand_L", "wpn", "Left Hand Follows Weapon"),
}
SIDES = ("L", "R")
UP = Vector((0.0, 0.0, 1.0))
# limb -> (upper, lower, end, pole label, fallback pole direction when the limb is straight)
LIMBS = {
    "arm": ("arm_upper", "arm_lower", "hand", "elbow", Vector((0.0, 1.0, 0.0))),
    "leg": ("leg_upper", "leg_lower", "ankle", "knee", Vector((0.0, -1.0, 0.0))),
}
TOE_BONE = "ball"
POLE_DISTANCE = 0.45
HEEL_BEHIND_ANKLE = 0.06
ROLL_LIMIT = math.radians(90)
# (twist bone, bone whose twist it shares, share of that twist). A negative share
# undoes part of the parent's own twist. The game's weights are not in its files;
# these follow where each bone's skin weights sit along the limb.
TWISTS = (
    ("arm_lower_{side}_TWIST", "hand_{side}", 0.4),
    ("arm_lower_{side}_TWIST1", "hand_{side}", 0.8),
    ("arm_upper_{side}_TWIST", "arm_upper_{side}", -2 / 3),
    ("arm_upper_{side}_TWIST1", "arm_upper_{side}", -1 / 3),
    ("leg_upper_{side}_TWIST", "leg_upper_{side}", -2 / 3),
    ("leg_upper_{side}_TWIST1", "leg_upper_{side}", -1 / 3),
)


def switch_prop(limb: str, side: str) -> str:
    return f"ik_{limb}_{side}"


def limb_bones(limb: str, side: str) -> dict[str, str]:
    upper, lower, end, joint, _ = LIMBS[limb]
    names = {
        "upper": f"{upper}_{side}",
        "lower": f"{lower}_{side}",
        "end": f"{end}_{side}",
        "mch_upper": f"MCH-{upper}_{side}",
        "mch_lower": f"MCH-{lower}_{side}",
        "tgt_upper": f"TGT-{upper}_{side}",
        "tgt_lower": f"TGT-{lower}_{side}",
        "ik": f"IK-{end}_{side}",
        "pole": f"IK-{joint}_{side}",
        # What the chain reaches for and the end bone follows. The hand control is
        # itself a rest copy of the hand; the foot's sits under the roll pivots.
        "target": f"IK-{end}_{side}",
    }
    if limb == "leg":
        names.update(
            target=f"TGT-{end}_{side}",
            toe=f"{TOE_BONE}_{side}",
            ik_toe=f"IK-toe_{side}",
            roll=f"IK-roll_{side}",
            mch_heel=f"MCH-heel_{side}",
            mch_ball=f"MCH-ball_{side}",
        )
    return names


def follow_pairs(names: dict[str, str]) -> list[tuple[str, str]]:
    """(game bone, the bone it follows under IK), parents first."""
    pairs = [(names["upper"], names["tgt_upper"]), (names["lower"], names["tgt_lower"]), (names["end"], names["target"])]
    if "toe" in names:
        pairs.append((names["toe"], names["ik_toe"]))
    return pairs


def ik_controls(names: dict[str, str]) -> list[str]:
    return [names[key] for key in ("ik", "pole", "roll", "ik_toe") if key in names]


def has_rig(armature: bpy.types.Object) -> bool:
    return armature.get(RIG_VERSION_PROP) == RIG_VERSION


def rigged_limbs(armature: bpy.types.Object) -> list[tuple[str, str]]:
    return [
        (limb, side)
        for limb in LIMBS
        for side in SIDES
        if limb_bones(limb, side)["ik"] in armature.pose.bones
    ]


def pole_position(a: Vector, b: Vector, c: Vector, fallback: Vector) -> Vector:
    """A point out from joint b, in the plane of the limb a-b-c, on the outside of the bend."""
    axis = c - a
    direction = fallback
    if axis.length > 1e-6:
        on_axis = a + axis * ((b - a).dot(axis) / axis.length_squared)
        bend = b - on_axis
        if bend.length > 1e-4:
            direction = bend.normalized()
    return b + direction * POLE_DISTANCE


def _signed_angle(u: Vector, v: Vector, normal: Vector) -> float:
    angle = u.angle(v)
    return -angle if u.cross(v).angle(normal) < 1.0 else angle


def _pole_angle(base, ik, pole: Vector) -> float:
    pole_normal = (ik.tail - base.head).cross(pole - base.head)
    projected = pole_normal.cross(base.tail - base.head)
    return _signed_angle(base.x_axis, projected, base.tail - base.head)


def _copy_bone(edit_bones, source, name: str, parent):
    bone = edit_bones.new(name)
    bone.head, bone.tail, bone.roll = source.head, source.tail, source.roll
    bone.parent = parent
    bone.use_deform = False
    return bone


def _new_bone(edit_bones, name: str, head: Vector, tail: Vector, parent, z_towards: Vector | None = None):
    bone = edit_bones.new(name)
    bone.head, bone.tail = head, tail
    bone.parent = parent
    bone.use_deform = False
    if z_towards is not None:
        bone.align_roll(z_towards)
    return bone


def _drive(armature: bpy.types.Object, owner, path: str, prop: str, expression: str = "") -> None:
    """Drive a property from one of the armature's switches, without needing Python scripts enabled."""
    driver = owner.driver_add(path).driver
    variable = driver.variables.new()
    variable.name = "ik"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = armature
    variable.targets[0].data_path = f'["{prop}"]'
    if expression:
        driver.type = "SCRIPTED"
        driver.expression = expression  # simple expressions are evaluated without Python
    else:
        driver.type = "AVERAGE"


def _axis_along(bone: bpy.types.Bone, direction: Vector) -> tuple[str, float]:
    """The bone's local axis closest to a direction, and whether it points with or against it."""
    local = bone.matrix_local.to_3x3().inverted() @ direction
    index = max(range(3), key=lambda i: abs(local[i]))
    return "xyz"[index], 1.0 if local[index] > 0 else -1.0


def _add_twists(armature: bpy.types.Object) -> int:
    bones, pose_bones = armature.data.bones, armature.pose.bones
    count = 0
    for side in SIDES:
        for twist_name, source_name, share in TWISTS:
            twist_name, source_name = twist_name.format(side=side), source_name.format(side=side)
            if twist_name not in bones or source_name not in bones or bones[twist_name].parent is None:
                continue
            twist, source = bones[twist_name], bones[source_name]
            along = twist.head_local - twist.parent.head_local  # twist bones sit part-way down the limb
            if along.length < 1e-5:
                continue
            from_axis, from_sign = _axis_along(source, along)
            to_axis, to_sign = _axis_along(twist, along)
            turn = math.pi * share * from_sign * to_sign
            constraint = pose_bones[twist_name].constraints.new("TRANSFORM")
            constraint.name = TWIST_CONSTRAINT
            constraint.target = armature
            constraint.subtarget = source_name
            constraint.target_space = constraint.owner_space = "LOCAL"
            constraint.map_from = constraint.map_to = "ROTATION"
            constraint.from_rotation_mode = f"SWING_TWIST_{from_axis.upper()}"
            constraint.use_motion_extrapolate = True
            setattr(constraint, f"from_min_{from_axis}_rot", -math.pi)
            setattr(constraint, f"from_max_{from_axis}_rot", math.pi)
            setattr(constraint, f"map_to_{to_axis}_from", from_axis.upper())
            setattr(constraint, f"to_min_{to_axis}_rot", -turn)
            setattr(constraint, f"to_max_{to_axis}_rot", turn)
            count += 1
    return count


def build_rig(context: bpy.types.Context, armature: bpy.types.Object) -> list[tuple[str, str]]:
    """Add IK controls to an imported agent. Returns the limbs that were rigged."""
    if has_rig(armature):
        return rigged_limbs(armature)
    if RIG_VERSION_PROP in armature.keys():
        remove_rig(context, armature)  # built by an older version
    bones = armature.data.bones
    if ROOT_BONE not in bones:
        raise RuntimeError(f"This skeleton has no '{ROOT_BONE}' bone, so it is not a CS2 agent.")
    limbs = [
        (limb, side)
        for limb in LIMBS
        for side in SIDES
        if all(limb_bones(limb, side)[k] in bones for k in ("upper", "lower", "end"))
    ]
    if not limbs:
        raise RuntimeError("No arm or leg bones were found on this skeleton.")

    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = armature.data.edit_bones
    root = edit_bones[ROOT_BONE]
    pole_angles = {}
    with_roll = set()
    for limb, side in limbs:
        names = limb_bones(limb, side)
        upper, lower, end = edit_bones[names["upper"]], edit_bones[names["lower"]], edit_bones[names["end"]]
        a, b, c = upper.head.copy(), lower.head.copy(), end.head.copy()
        pole_at = pole_position(a, b, c, LIMBS[limb][4])

        plane_normal = (b - a).cross(pole_at - a)
        mch_upper = _new_bone(edit_bones, names["mch_upper"], a, b, upper.parent, plane_normal)
        mch_lower = _new_bone(edit_bones, names["mch_lower"], b, c, mch_upper, plane_normal)
        mch_lower.use_connect = True
        ik = _copy_bone(edit_bones, end, names["ik"], root)
        _new_bone(edit_bones, names["pole"], pole_at, pole_at + Vector((0.0, 0.0, 0.08)), root)
        _copy_bone(edit_bones, upper, names["tgt_upper"], mch_upper)
        _copy_bone(edit_bones, lower, names["tgt_lower"], mch_lower)
        pole_angles[(limb, side)] = _pole_angle(mch_upper, mch_lower, pole_at)

        toe = edit_bones.get(names.get("toe", ""))
        if limb == "leg":
            # Roll pivots: the foot control carries the heel pivot, which carries the
            # ball pivot, which carries the ankle. Toes hang off the heel pivot so
            # they stay on the ground while the heel lifts.
            ball_at = toe.head.copy() if toe else c + rig_layout.FORWARD * 0.12
            forward = Vector((ball_at.x - c.x, ball_at.y - c.y, 0.0))
            forward = forward.normalized() if forward.length > 1e-4 else rig_layout.FORWARD.copy()
            heel_at = Vector((c.x, c.y, 0.0)) - forward * HEEL_BEHIND_ANKLE
            step = forward * 0.08
            heel = _new_bone(edit_bones, names["mch_heel"], heel_at, heel_at + step, ik, UP)
            ball = _new_bone(edit_bones, names["mch_ball"], ball_at, ball_at + step, heel, UP)
            roll_at = heel_at - forward * 0.08 + UP * 0.05
            _new_bone(edit_bones, names["roll"], roll_at, roll_at + step, ik, UP)
            _copy_bone(edit_bones, end, names["target"], ball)
            if toe:
                _copy_bone(edit_bones, toe, names["ik_toe"], heel)
            with_roll.add((limb, side))
    _new_bone(edit_bones, rig_layout.ROOT_DISPLAY_BONE, root.head, root.head + Vector((0.0, 0.2, 0.0)), None)
    bpy.ops.object.mode_set(mode="POSE")

    pose_bones = armature.pose.bones
    follow_root = pose_bones[rig_layout.ROOT_DISPLAY_BONE].constraints.new("COPY_LOCATION")
    follow_root.target = armature
    follow_root.subtarget = ROOT_BONE
    for limb, side in limbs:
        names = limb_bones(limb, side)
        prop = switch_prop(limb, side)
        armature[prop] = 0.0
        ui = armature.id_properties_ui(prop)
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description="0 = game clip / FK, 1 = IK control")

        solver = pose_bones[names["mch_lower"]].constraints.new("IK")
        solver.target = armature
        solver.subtarget = names["target"]
        solver.pole_target = armature
        solver.pole_subtarget = names["pole"]
        solver.pole_angle = pole_angles[(limb, side)]
        solver.chain_count = 2
        solver.use_stretch = False

        if (limb, side) in with_roll:
            roll = pose_bones[names["roll"]]
            roll.rotation_mode = "XYZ"
            roll.lock_location = roll.lock_scale = (True, True, True)
            roll.lock_rotation = (False, True, True)
            # Rolling one way tips the foot back on its heel, the other way lifts the heel off the ball.
            for pivot, low, high in ((names["mch_heel"], 0.0, ROLL_LIMIT), (names["mch_ball"], -ROLL_LIMIT, 0.0)):
                copy = pose_bones[pivot].constraints.new("COPY_ROTATION")
                copy.name = ROLL_CONSTRAINT
                copy.target = armature
                copy.subtarget = names["roll"]
                copy.target_space = copy.owner_space = "LOCAL"
                copy.use_y = copy.use_z = False
                limit = pose_bones[pivot].constraints.new("LIMIT_ROTATION")
                limit.name = ROLL_CONSTRAINT
                limit.owner_space = "LOCAL"
                limit.use_limit_x = True
                limit.min_x, limit.max_x = low, high

        for game, target in follow_pairs(names):
            if game not in pose_bones or target not in pose_bones:
                continue
            follow = pose_bones[game].constraints.new("COPY_TRANSFORMS")
            follow.name = FOLLOW_CONSTRAINT
            follow.target = armature
            follow.subtarget = target
            _drive(armature, follow, "influence", prop)
            # Show only the controls that currently drive the limb: IK controls sit at
            # stale positions while the clip drives it, and FK rings do nothing under IK.
            _drive(armature, armature, f'pose.bones["{game}"].hide', prop, "ik >= 0.5")

        pose_bones[names["pole"]].lock_rotation = (True, True, True)
        pose_bones[names["pole"]].lock_scale = (True, True, True)
        for control in ik_controls(names):
            if control in pose_bones:
                _drive(armature, armature, f'pose.bones["{control}"].hide', prop, "ik < 0.5")

    for prop, (owner, target, label) in FOLLOWS.items():
        if owner not in pose_bones or target not in pose_bones:
            continue
        armature[prop] = 0.0
        armature.id_properties_ui(prop).update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=label)
        follow = pose_bones[owner].constraints.new("CHILD_OF")
        follow.name = PARENT_CONSTRAINT
        follow.target = armature
        follow.subtarget = target
        _drive(armature, follow, "influence", prop)

    _add_twists(armature)
    rig_layout.organise(armature)
    rig_layout.add_shapes(armature)
    armature[RIG_VERSION_PROP] = RIG_VERSION
    return limbs


def remove_rig(context: bpy.types.Context, armature: bpy.types.Object) -> None:
    """Take the controls off again, leaving the game skeleton and its animation as imported."""
    context.view_layer.objects.active = armature
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for pose_bone in armature.pose.bones:
        for constraint in [c for c in pose_bone.constraints if c.name.startswith(CONSTRAINT_PREFIX)]:
            constraint.driver_remove("influence")
            pose_bone.constraints.remove(constraint)
    rig_layout.remove_shapes(armature)
    for pose_bone in armature.pose.bones:
        if armature.driver_remove(f'pose.bones["{pose_bone.name}"].hide'):
            pose_bone.hide = False
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = armature.data.edit_bones
    for bone in [b for b in edit_bones if b.name.startswith(RIG_BONE_PREFIXES)]:
        edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")
    for key in [k for k in armature.keys() if k.startswith("ik_") or k in FOLLOWS or k == RIG_VERSION_PROP]:
        del armature[key]
    rig_layout.organise(armature)
    armature.data.display_type = "OCTAHEDRAL"
    armature.show_in_front = False


def _set_matrix(context, pose_bone, matrix: Matrix) -> None:
    pose_bone.matrix = matrix
    context.view_layer.update()  # children read the parent's evaluated matrix


def snap_ik_to_fk(context: bpy.types.Context, armature: bpy.types.Object, limb: str, side: str) -> None:
    """Move the IK controls onto the current pose and switch the limb to IK."""
    names = limb_bones(limb, side)
    pose_bones = armature.pose.bones
    upper, lower, end = (pose_bones[names[k]] for k in ("upper", "lower", "end"))
    end_matrix = end.matrix.copy()
    toe = pose_bones.get(names.get("toe", ""))
    toe_matrix = toe.matrix.copy() if toe else None
    a, b, c = upper.head.copy(), lower.head.copy(), end.head.copy()

    # Clips can move the shoulder/hip bone itself; keep the chain's start on it.
    mch_upper = pose_bones[names["mch_upper"]]
    start = mch_upper.matrix.copy()
    start.translation = a
    _set_matrix(context, mch_upper, start)
    if "roll" in names and names["roll"] in pose_bones:
        pose_bones[names["roll"]].matrix_basis.identity()  # the foot control alone carries the pose
    _set_matrix(context, pose_bones[names["ik"]], end_matrix)
    if toe_matrix is not None and names["ik_toe"] in pose_bones:
        _set_matrix(context, pose_bones[names["ik_toe"]], toe_matrix)
    pole = pose_bones[names["pole"]]
    pole_matrix = pole.bone.matrix_local.copy()
    pole_matrix.translation = pole_position(a, b, c, LIMBS[limb][4])
    _set_matrix(context, pole, pole_matrix)
    armature[switch_prop(limb, side)] = 1.0
    armature.update_tag()
    context.view_layer.update()


def snap_fk_to_ik(context: bpy.types.Context, armature: bpy.types.Object, limb: str, side: str) -> None:
    """Copy the IK result onto the game bones and switch the limb back to FK."""
    pose_bones = armature.pose.bones
    chain = [pose_bones[game] for game, _ in follow_pairs(limb_bones(limb, side)) if game in pose_bones]
    solved = [bone.matrix.copy() for bone in chain]
    armature[switch_prop(limb, side)] = 0.0
    armature.update_tag()
    context.view_layer.update()
    for bone, matrix in zip(chain, solved):
        _set_matrix(context, bone, matrix)


def follows(armature: bpy.types.Object) -> list[str]:
    """The follow switches this rig has."""
    return [prop for prop in FOLLOWS if prop in armature.keys()]


def set_follow(context: bpy.types.Context, armature: bpy.types.Object, prop: str, on: bool) -> None:
    """Make one bone follow another, or let it go, without moving it."""
    owner_name, target_name, _ = FOLLOWS[prop]
    pose_bones = armature.pose.bones
    owner, target = pose_bones[owner_name], pose_bones[target_name]
    if on and owner_name.startswith("IK-hand_"):
        # The hand control only matters under IK; bring it onto the hand first.
        side = owner_name.rsplit("_", 1)[1]
        if armature[switch_prop("arm", side)] < 0.5:
            snap_ik_to_fk(context, armature, "arm", side)
    kept = owner.matrix.copy()
    if on:
        # Start following from where both bones are now.
        owner.constraints[PARENT_CONSTRAINT].inverse_matrix = (armature.matrix_world @ target.matrix).inverted()
    armature[prop] = 1.0 if on else 0.0
    armature.update_tag()
    context.view_layer.update()
    if not on:
        _set_matrix(context, owner, kept)