"""Check that finishes survive saving and reopening a .blend.

    blender --background --factory-startup --python tools/reopen_check.py -- <work_dir> [old.blend]

Applies a finish, saves, reopens, applies another and removes it, which must
bring back the weapon's stock material. With an old .blend (one saved by an
earlier version, whose stock material may be gone), also checks that a new
finish can still go on it.
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer, paint  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
work = Path(args[0])
old_file = Path(args[1]) if len(args) > 1 else None
work.mkdir(parents=True, exist_ok=True)


def start():
    if not hasattr(bpy.types.WindowManager, "cs2rig_skin"):
        cs2_rigs.register()
    assert bpy.ops.cs2rig.refresh() == {"FINISHED"}


def agent():
    found = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and importer.AGENT_KEY_PROP in obj.keys()]
    assert len(found) == 1
    bpy.context.view_layer.objects.active = found[0]
    return found[0]


def body_materials(armature):
    return {obj.name: [slot.material.name if slot.material else None for slot in obj.material_slots] for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"}


def apply(name):
    bpy.context.window_manager.cs2rig_skin = name
    assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
    assert paint.worn_skin(agent()) == name


if old_file is not None:
    bpy.ops.wm.open_mainfile(filepath=str(old_file))
    start()
    apply("cu_ak47_asiimov")
    apply("hy_veneto_purple")
    print("REOPEN old file takes new finishes:", body_materials(agent()))

bpy.ops.wm.read_factory_settings(use_empty=True)
start()
wm = bpy.context.window_manager
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
wm.cs2rig_weapon = "ak47"
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
stock = body_materials(agent())
apply("gs_ak47_bloodsport")
path = work / "reopen.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
start()
apply("cu_ak47_asiimov")
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
start()
armature = agent()
assert bpy.ops.cs2rig.remove_skin() == {"FINISHED"}
assert body_materials(armature) == stock, (body_materials(armature), stock)
assert not [m.name for m in bpy.data.materials if paint.BASE_MATERIAL_PROP in m.keys() and m.users]
print("REOPEN OK")
