"""Put finishes on a held weapon in a real Blender and render them for a visual check.

    blender --background --factory-startup --python tools/skin_check.py -- <out_dir> [weapon]

Applies one finish made for the old weapon body and one made for the current
body, checks that removing a finish restores the stock materials, and lists how
many finishes each weapon is offered.
"""

import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer, paint  # noqa: E402
from cs2_rigs.core import skins  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
weapon_key = args[1] if len(args) > 1 else "ak47"
out_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
scene = context.scene

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
kits = list(cs2_rigs._kits.values())
print("SKIN catalogue:", len(kits), "finishes,", sum(1 for k in kits if skins.is_painted(k)), "painted")
offered = {key: skins.kits_for(kits, skins.item_name(key)) for key in cs2_rigs._weapons}
# Grenades, equipment and the two stock knives have no finishes in the game.
none = [k for k, found in offered.items() if not found and cs2_rigs._weapons[k].kind != "other" and "knife_default" not in k]
print("SKIN weapons with no finishes at all:", none)
assert not none, "a weapon's item name is wrong"
print("SKIN painted finishes per weapon:", {key: sum(1 for k in found if skins.is_painted(k)) for key, found in offered.items() if found})

wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object
wm.cs2rig_weapon = weapon_key
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
wm.cs2rig_clip = "world/rifle/_default_rifle/run_n_rifle"
assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}
scene.frame_set(1)
materials_before = set(bpy.data.materials)
stock = {obj.name: ([slot.material for slot in obj.material_slots], obj.hide_render) for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"}

meshes = [obj for obj in importer.held_weapon_objects(armature) if obj.type == "MESH" and not obj.hide_render]
corners = [obj.matrix_world @ Vector(c) for obj in meshes for c in obj.bound_box]
middle = sum(corners, Vector()) / len(corners)
camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
camera.data.lens = 60
camera.location = middle + Vector((-1.5, -0.2, 0.15))
camera.rotation_euler = (middle - camera.location).to_track_quat("-Z", "Y").to_euler()
scene.collection.objects.link(camera)
scene.camera = camera
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 4
sun.rotation_euler = (0.9, -0.6, -1.2)
scene.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
scene.world = world
for node in world.node_tree.nodes:
    if node.type == "BACKGROUND":
        node.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 1100, 600

painted = [k for k in offered[weapon_key] if skins.is_painted(k)]
old_body = next(k for k in painted if skins.uses_legacy_model(k))
new_body = next((k for k in painted if not skins.uses_legacy_model(k)), None)
for kit in filter(None, (old_body, new_body)):
    wm.cs2rig_skin = kit.name
    assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
    shown = [obj.name for obj in importer.held_weapon_objects(armature) if obj.type == "MESH" and not obj.hide_render]
    print(f"SKIN applied {kit.label} ({kit.name}, style {kit.style}) on {shown}")
    assert paint.worn_skin(armature) == kit.name and len(shown) == 1
    assert ("legacy" in shown[0]) == skins.uses_legacy_model(kit)
    scene.render.filepath = str(out_dir / f"skin_{weapon_key}_{kit.name}.png")
    bpy.ops.render.render(write_still=True)

assert bpy.ops.cs2rig.remove_skin() == {"FINISHED"}
now = {obj.name: ([slot.material for slot in obj.material_slots], obj.hide_render) for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"}
assert now == stock, "removing the finish did not restore the weapon"
assert set(bpy.data.materials) == materials_before, "a painted material was left behind"
assert not paint.worn_skin(armature)
print("SKIN OK")
