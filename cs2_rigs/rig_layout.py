"""How the rig looks and is organised: control shapes and bone collections.

Imported bones point wherever the importer guessed, so drawing them as bones is
unreadable. Every bone an animator touches gets its own wire shape, built in
armature space from the real joint positions and then moved into that bone's
space. Everything else goes into hidden collections.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

WIDGET_COLLECTION = "CS2 Rigs Widgets"
WIDGET_PREFIX = "WGT-"
# Clips rotate the root bone, so its ring is drawn at a bone that only follows its position.
ROOT_DISPLAY_BONE = "MCH-root_flat"
SIDES = ("L", "R")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")
UP = Vector((0.0, 0.0, 1.0))
FORWARD = Vector((0.0, -1.0, 0.0))  # agents face -Y

# Collections in display order: (name, visible)
COLLECTIONS = (
    ("Root", True),
    ("Torso", True),
    ("Arms FK", True),
    ("Legs FK", True),
    ("IK", True),
    ("Fingers", True),
    ("Weapon", True),
    ("Secondary", False),  # jiggle, twist, eye and attachment bones the game drives
    ("Mechanism", False),  # rig internals
)
TORSO = ("pelvis", "spine_0", "spine_1", "spine_2", "spine_3", "neck_0", "head_0")
ARM = ("clavicle", "arm_upper", "arm_lower", "hand")
LEG = ("leg_upper", "leg_lower", "ankle", "ball")
WEAPON = ("wpnPivot", "wpn")
# Rings around a bone: (bone, next joint, radius, position along the way to the next joint)
TORSO_RINGS = (
    ("pelvis", "spine_0", 0.23, 0.0),
    ("spine_0", "spine_1", 0.19, 0.3),
    ("spine_1", "spine_2", 0.19, 0.3),
    ("spine_2", "spine_3", 0.20, 0.3),
    ("spine_3", "neck_0", 0.21, 0.4),
    ("neck_0", "head_0", 0.085, 0.4),
)
LIMB_RINGS = (
    ("clavicle", "arm_upper", 0.055, 0.6),
    ("arm_upper", "arm_lower", 0.075, 0.45),
    ("arm_lower", "hand", 0.06, 0.45),
    ("hand", "finger_middle_0", 0.05, 0.6),
    ("leg_upper", "leg_lower", 0.11, 0.4),
    ("leg_lower", "ankle", 0.08, 0.4),
    ("ankle", "ball", 0.07, 0.5),
)
PALETTE = {"L": "THEME04", "R": "THEME01", "C": "THEME09", "ROOT": "THEME03"}


def collection_for(name: str) -> str:
    """Which collection a bone belongs in, from its name alone."""
    if name.startswith(("MCH-", "TGT-")):
        return "Mechanism"
    if name.startswith("IK-"):
        return "IK"
    if name == "root_motion":
        return "Root"
    if name in TORSO:
        return "Torso"
    if name in WEAPON:
        return "Weapon"
    stem, _, side = name.rpartition("_")
    if side in SIDES:
        if stem in ARM:
            return "Arms FK"
        if stem in LEG:
            return "Legs FK"
        # finger_<name>_<0-2>; the 'meta' palm bones stay with the game-driven bones
        parts = stem.split("_")
        if len(parts) == 3 and parts[0] == "finger" and parts[2].isdigit():
            return "Fingers"
    return "Secondary"


def organise(armature: bpy.types.Object) -> None:
    """Put every bone in exactly one collection; a bone in none would always show."""
    data = armature.data
    wanted = {name for name, _ in COLLECTIONS}
    collections = {}
    for name, visible in COLLECTIONS:
        collection = data.collections.get(name) or data.collections.new(name)
        collection.is_visible = visible
        collections[name] = collection
    for bone in data.bones:
        for old in list(bone.collections):
            old.unassign(bone)
        collections[collection_for(bone.name)].assign(bone)
    for collection in list(data.collections_all):
        if collection.name not in wanted and not collection.bones and not collection.children:
            data.collections.remove(collection)
    data.display_type = "STICK"
    armature.show_in_front = True


def _ring(centre: Vector, normal: Vector, radius: float, segments: int = 24):
    normal = normal.normalized()
    side = normal.orthogonal().normalized()
    other = normal.cross(side)
    verts = [
        centre + (side * math.cos(2 * math.pi * i / segments) + other * math.sin(2 * math.pi * i / segments)) * radius
        for i in range(segments)
    ]
    return verts, [(i, (i + 1) % segments) for i in range(segments)]


def _box(centre: Vector, half: Vector):
    verts = [centre + Vector((half.x * x, half.y * y, half.z * z)) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    signs = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if sum(signs[a][i] != signs[b][i] for i in range(3)) == 1]
    return verts, edges


def _diamond(centre: Vector, radius: float):
    axes = [Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))]
    verts = [centre + axis * (radius * sign) for axis in axes for sign in (1, -1)]
    return verts, [(a, b) for a in range(6) for b in range(a + 1, 6) if a // 2 != b // 2]


def _join(*shapes):
    verts, edges = [], []
    for shape_verts, shape_edges in shapes:
        offset = len(verts)
        verts += shape_verts
        edges += [(a + offset, b + offset) for a, b in shape_edges]
    return verts, edges


def _widget_collection() -> bpy.types.Collection:
    collection = bpy.data.collections.get(WIDGET_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(WIDGET_COLLECTION)
        collection.hide_viewport = True
        collection.hide_render = True
    return collection


def _set_shape(armature: bpy.types.Object, bone_name: str, shape, palette: str, drawn_at: str = "") -> None:
    """Give a bone a wire shape described in armature space.

    `drawn_at` names another bone to draw the shape at, for a control whose own
    rotation would make its shape misleading.
    """
    verts, edges = shape
    bone = armature.data.bones[drawn_at or bone_name]
    to_bone = bone.matrix_local.inverted()
    name = f"{WIDGET_PREFIX}{armature.name}-{bone_name}"
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(to_bone @ v) for v in verts], edges, [])
    obj = bpy.data.objects.new(name, mesh)
    _widget_collection().objects.link(obj)
    pose_bone = armature.pose.bones[bone_name]
    pose_bone.custom_shape = obj
    pose_bone.custom_shape_transform = armature.pose.bones[drawn_at] if drawn_at else None
    pose_bone.use_custom_shape_bone_size = False
    pose_bone.custom_shape_scale_xyz = (1.0, 1.0, 1.0)
    pose_bone.color.palette = palette


def _ring_towards(bones, name: str, towards: str, radius: float, along: float):
    start = bones[name].head_local
    direction = bones[towards].head_local - start
    if direction.length < 1e-5:
        direction = UP
    return _ring(start + direction * along, direction, radius)


def add_shapes(armature: bpy.types.Object) -> int:
    """Give every visible control its shape. Returns how many bones got one."""
    bones = armature.data.bones
    count = 0

    def shape(name, geometry, palette, drawn_at=""):
        nonlocal count
        if name in bones:
            _set_shape(armature, name, geometry, palette, drawn_at if drawn_at in bones else "")
            count += 1

    if "root_motion" in bones:
        origin = bones["root_motion"].head_local
        arrow = (
            [origin + FORWARD * 0.5, origin + FORWARD * 0.72, origin + FORWARD * 0.6 + Vector((0.07, 0, 0)), origin + FORWARD * 0.6 - Vector((0.07, 0, 0))],
            [(0, 1), (1, 2), (1, 3)],
        )
        shape("root_motion", _join(_ring(origin, UP, 0.5, 32), arrow), PALETTE["ROOT"], ROOT_DISPLAY_BONE)

    for name, towards, radius, along in TORSO_RINGS:
        if name in bones and towards in bones:
            shape(name, _ring_towards(bones, name, towards, radius, along), PALETTE["C"])
    if "head_0" in bones:
        shape("head_0", _ring(bones["head_0"].head_local + UP * 0.1, UP, 0.14), PALETTE["C"])

    for side in SIDES:
        colour = PALETTE[side]
        for stem, towards, radius, along in LIMB_RINGS:
            name, target = f"{stem}_{side}", f"{towards}_{side}"
            if name in bones and target in bones:
                shape(name, _ring_towards(bones, name, target, radius, along), colour)
        ball = f"ball_{side}"
        if ball in bones:
            shape(ball, _ring(bones[ball].head_local + FORWARD * 0.05, FORWARD, 0.05), colour)

        for finger in FINGERS:
            chain = [f"finger_{finger}_{i}_{side}" for i in range(3)]
            chain = [name for name in chain if name in bones]
            for index, name in enumerate(chain):
                start = bones[name].head_local
                if index + 1 < len(chain):
                    direction = bones[chain[index + 1]].head_local - start
                elif index > 0:
                    # The tip has no child joint; carry on in the last segment's direction.
                    previous = start - bones[chain[index - 1]].head_local
                    direction = previous.normalized() * previous.length * 0.8
                else:
                    continue
                shape(name, _ring(start + direction * 0.5, direction, 0.012, 12), colour)

        hand, foot = f"IK-hand_{side}", f"IK-ankle_{side}"
        if hand in bones:
            shape(hand, _box(bones[hand].head_local, Vector((0.055, 0.055, 0.055))), colour)
        if foot in bones:
            ankle = bones[foot].head_local
            toe = bones[ball].head_local if ball in bones else ankle + FORWARD * 0.15
            # A box around the whole foot, from behind the heel to past the toes, down to the ground.
            low = Vector((ankle.x - 0.065, min(toe.y, ankle.y) - 0.09, 0.0))
            high = Vector((ankle.x + 0.065, max(toe.y, ankle.y) + 0.07, ankle.z + 0.03))
            shape(foot, _box((low + high) / 2, (high - low) / 2), colour)
        toe, roll = f"IK-toe_{side}", f"IK-roll_{side}"
        if toe in bones:
            shape(toe, _ring(bones[toe].head_local + FORWARD * 0.05, FORWARD, 0.06), colour)
        if roll in bones:
            # A wheel behind the heel, turning about the axis the foot rolls on.
            shape(roll, _ring(bones[roll].head_local, bones[roll].matrix_local.to_3x3().col[0], 0.045, 16), colour)
        for joint in ("elbow", "knee"):
            pole = f"IK-{joint}_{side}"
            if pole in bones:
                shape(pole, _diamond(bones[pole].head_local, 0.035), colour)

    if "wpnPivot" in bones:
        shape("wpnPivot", _diamond(bones["wpnPivot"].head_local, 0.05), PALETTE["ROOT"])
    if "wpn" in bones:
        shape("wpn", _box(bones["wpn"].head_local, Vector((0.04, 0.04, 0.04))), PALETTE["ROOT"])
    return count


def remove_shapes(armature: bpy.types.Object) -> None:
    widgets = set()
    for pose_bone in armature.pose.bones:
        if pose_bone.custom_shape is not None and pose_bone.custom_shape.name.startswith(WIDGET_PREFIX):
            widgets.add(pose_bone.custom_shape)
            pose_bone.custom_shape = None
    # A duplicated agent shares its widgets with the original: keep those still in use.
    in_use = {
        pose_bone.custom_shape
        for obj in bpy.data.objects
        if obj.type == "ARMATURE" and obj.pose is not None
        for pose_bone in obj.pose.bones
        if pose_bone.custom_shape is not None
    }
    bpy.data.batch_remove([widget for widget in widgets if widget not in in_use])
