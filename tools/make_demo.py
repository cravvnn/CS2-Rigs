"""Build a demo scene: an agent with a weapon, a game clip and IK controls.

    blender --background --factory-startup --python tools/make_demo.py -- <out.blend> [agent] [weapon] [clip] [finish]
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out = Path(args[0])
agent = args[1] if len(args) > 1 else "ctm_sas/ctm_sas"
weapon = args[2] if len(args) > 2 else "ak47"
clip = args[3] if len(args) > 3 else "world/rifle/_default_rifle/run_n_rifle"
finish = args[4] if len(args) > 4 else ""  # a paint kit name such as cu_ak47_cobra

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
wm = bpy.context.window_manager
scene = bpy.context.scene

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
wm.cs2rig_agent = agent
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = bpy.context.active_object
wm.cs2rig_weapon = weapon
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
if finish:
    wm.cs2rig_skin = finish
    assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
wm.cs2rig_clip = clip
assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}
assert bpy.ops.cs2rig.build_rig() == {"FINISHED"}
bpy.ops.object.mode_set(mode="OBJECT")

sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 4
sun.rotation_euler = (0.9, 0.2, 0.6)
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("World")
world.color = (0.25, 0.27, 0.3)
scene.world = world
scene.render.engine = "BLENDER_EEVEE"
scene.frame_set(scene.frame_start)

for screen in bpy.data.screens:
    for area in screen.areas:
        for space in area.spaces:
            if space.type == "VIEW_3D":
                space.shading.type = "MATERIAL"
                space.show_region_ui = True

out.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out))
print("DEMO OK", out)
