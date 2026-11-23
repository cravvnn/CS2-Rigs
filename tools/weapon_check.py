"""Check weapon attachment in a real Blender and render it for a visual check.

    blender --background --factory-startup --python tools/weapon_check.py -- <out_dir> [weapon] [clip]

The weapon must stay in the hands that hold it over the clip; a wrong attachment frame
leaves it at the feet or pointing the wrong way.
"""

import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
weapon_key = args[1] if len(args) > 1 else "ak47"
clip = args[2] if len(args) > 2 else "world/rifle/_default_rifle/run_n_rifle"
out_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
scene = context.scene

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
print("WPN weapons:", len(cs2_rigs._weapon_items), [i[0] for i in cs2_rigs._weapon_items])
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object
wm.cs2rig_clip = clip
assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}
wm.cs2rig_weapon = weapon_key
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
held = importer.held_weapon_objects(armature)
meshes = [o for o in held if o.type == "MESH" and not o.hide_render]
print("WPN held:", [o.name for o in held], "visible:", [o.name for o in meshes])
assert meshes

# Attaching again replaces rather than stacks.
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
assert len(importer.held_weapon_objects(armature)) == len(held)
meshes = [o for o in importer.held_weapon_objects(armature) if o.type == "MESH" and not o.hide_render]

# Only rifle clips put the left hand on the weapon.
hands = ("hand_R", "hand_L") if "/rifle/" in clip else ("hand_R",)
action = armature.animation_data.action
start, end = (int(v) for v in action.frame_range)
worst = 0.0
for frame in range(start, end + 1, max(1, (end - start) // 6)):
    scene.frame_set(frame)
    depsgraph = context.evaluated_depsgraph_get()
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.evaluated_get(depsgraph).bound_box]
    low = Vector(min(c[i] for c in corners) for i in range(3))
    high = Vector(max(c[i] for c in corners) for i in range(3))
    for hand in hands:
        p = armature.matrix_world @ armature.pose.bones[hand].head
        # Distance from the hand to the weapon's bounding box (0 when inside it).
        gap = Vector(max(low[i] - p[i], 0.0, p[i] - high[i]) for i in range(3)).length
        worst = max(worst, gap)
print("WPN size (m):", tuple(round(v, 3) for v in high - low))
print("WPN worst hand-to-weapon gap (m):", round(worst, 3))

scene.frame_set((start + end) // 2)
camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(camera)
centre = Vector((0.0, 0.0, 1.0))
camera.location = centre + Vector((-2.0, -3.0, 0.4))
camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = camera
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 4
sun.rotation_euler = (0.9, -0.3, -0.6)
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
world.color = (0.25, 0.27, 0.3)
scene.world = world
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 900, 1100
scene.render.filepath = str(out_dir / f"weapon_{weapon_key}.png")
bpy.ops.render.render(write_still=True)
assert worst < 0.12, "the weapon is not in the agent's hands"
print("WPN OK")
