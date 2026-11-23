"""End-to-end check in a real Blender: import an agent, load a clip, render a frame.

    blender --background --factory-startup --python tools/smoke_blender.py -- <out_dir> [agent] [clip]

Needs CS2 installed and CS2RIG_S2V_CLI pointing at Source2Viewer-CLI.
"""

import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
agent_key = args[1] if len(args) > 1 else "ctm_sas/ctm_sas"
clip = args[2] if len(args) > 2 else "world/rifle/_default_rifle/run_n_rifle"
out_dir.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
wm = bpy.context.window_manager

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
print("SMOKE agents:", len(cs2_rigs._agent_items), "clips:", len(wm.cs2rig_clips))
if clip not in wm.cs2rig_clips:
    candidates = [c.name for c in wm.cs2rig_clips if "run_n" in c.name][:40]
    raise SystemExit(f"SMOKE clip '{clip}' not in catalog. AK clips: {candidates}")

wm.cs2rig_agent = agent_key
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = bpy.context.active_object
meshes = [o for o in armature.children_recursive if o.type == "MESH"]
visible = [o for o in meshes if not o.hide_render]
print("SMOKE bones:", len(armature.data.bones), "meshes:", [o.name for o in meshes])
print("SMOKE materials:", sorted({s.material.name for o in visible for s in o.material_slots if s.material}))

wm.cs2rig_clip = clip
assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}
action = armature.animation_data.action
scene = bpy.context.scene
print("SMOKE action:", action.name, "frames:", tuple(action.frame_range), "objects:", len(bpy.data.objects))

# The pose must really change over the clip, or the action is not driving the skeleton.
def head_position(frame):
    scene.frame_set(frame)
    bone = armature.pose.bones.get("head_0") or armature.pose.bones[-1]
    return (armature.matrix_world @ bone.matrix).translation.copy()

start, end = (int(v) for v in action.frame_range)
travel = max((head_position(f) - head_position(start)).length for f in range(start, end + 1, max(1, (end - start) // 8)))
print("SMOKE head travel (m):", round(travel, 4))
assert travel > 0.005, "the clip does not move the skeleton"

scene.frame_set((start + end) // 2)
depsgraph = bpy.context.evaluated_depsgraph_get()
corners = [o.matrix_world @ Vector(c) for o in visible for c in o.evaluated_get(depsgraph).bound_box]
low = Vector(min(c[i] for c in corners) for i in range(3))
high = Vector(max(c[i] for c in corners) for i in range(3))
centre, size = (low + high) / 2, (high - low)
print("SMOKE size (m):", tuple(round(v, 3) for v in size))

camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
scene.collection.objects.link(camera)
camera.location = centre + Vector((size.length * 0.9, -size.length * 1.3, size.z * 0.15))
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
scene.render.filepath = str(out_dir / "smoke.png")
bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / "smoke.blend"))
print("SMOKE OK", scene.render.filepath)
