"""Put finishes on a held weapon in a real Blender and render them for a visual check.

    blender --background --factory-startup --python tools/skin_check.py -- <out_dir> [weapon] [wear] [kit ...]

Without kit names, applies one finish of every paint style the weapon is
offered, at the given wear (default 0.15). Checks that each finish shows on
exactly one body, the one it was made for, and that removing a finish restores
the stock materials.
"""

import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer, paint  # noqa: E402
from cs2_rigs.core import skins  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
weapon_key = args[1] if len(args) > 1 else "ak47"
wear = float(args[2]) if len(args) > 2 else 0.15
named = args[3:]
out_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
scene = context.scene

started = time.time()
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
print(f"SKIN refresh took {time.time() - started:.1f} s")
kits = list(cs2_rigs._kits.values())
usable = cs2_rigs._paintable
print("SKIN catalogue:", len(kits), "finishes,", len(usable), "with a recipe")
offered = {key: skins.kits_for(kits, skins.item_name(key)) for key in cs2_rigs._weapons}
# Grenades, equipment and the two stock knives have no finishes in the game.
none = [k for k, found in offered.items() if not found and cs2_rigs._weapons[k].kind != "other" and "knife_default" not in k]
print("SKIN weapons with no finishes at all:", none)
assert not none, "a weapon's item name is wrong"
unusable = sorted({kit.name for found in offered.values() for kit in found if kit.name not in usable})
print("SKIN weapon finishes without a recipe:", len(unusable), unusable[:12])

wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object
wm.cs2rig_weapon = weapon_key
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
scene.frame_set(1)
materials_before = set(bpy.data.materials)
stock = {obj.name: ([slot.material for slot in obj.material_slots], obj.hide_render) for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"}

meshes = [obj for obj in importer.held_weapon_objects(armature) if obj.type == "MESH" and not obj.hide_render]
corners = [obj.matrix_world @ Vector(c) for obj in meshes for c in obj.bound_box]
middle = sum(corners, Vector()) / len(corners)
reach = max((c - middle).length for c in corners)
# Look at the weapon side on: across its longest direction, level with it.
points = np.array([obj.matrix_world @ v.co for obj in meshes for v in list(obj.data.vertices)[::7]])
axes = np.linalg.eigh(np.cov((points - points.mean(axis=0)).T))[1]  # thinnest direction first
side, up, length = (Vector(axes[:, i]) for i in range(3))
root = next(obj for obj in importer.held_weapon_objects(armature) if importer.WEAPON_KEY_PROP in obj.keys())
if up.dot(root.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))) < 0:
    up = -up  # the weapon model's own up, wherever the rest pose points it
camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = reach * 2.1
camera.location = middle + side * reach * 5.0
camera.rotation_euler = Matrix((up.cross(side), up, side)).transposed().to_euler()
scene.collection.objects.link(camera)
scene.camera = camera
for index, turn in enumerate(((0.9, -0.6, -1.2), (1.1, 0.4, 1.9))):
    sun = bpy.data.objects.new(f"sun{index}", bpy.data.lights.new(f"sun{index}", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = turn
    scene.collection.objects.link(sun)
world = bpy.data.worlds.new("w")
scene.world = world
for node in world.node_tree.nodes:
    if node.type == "BACKGROUND":
        node.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
for obj in armature.children_recursive:
    if obj.type == "MESH" and obj not in importer.held_weapon_objects(armature):
        obj.hide_render = True  # only the weapon, so the hands do not cover it
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x, scene.render.resolution_y = 1400, 600

if named:
    chosen = [cs2_rigs._kits[name] for name in named]
else:
    chosen = []
    for kit in offered[weapon_key]:
        if kit.name in usable and (kit.style, skins.uses_legacy_model(kit)) not in {(k.style, skins.uses_legacy_model(k)) for k in chosen}:
            chosen.append(kit)
wm.cs2rig_wear = wear
bodies = [obj for obj in importer.held_weapon_objects(armature) if obj.type == "MESH" and "body" in obj.name]
for kit in chosen:
    wm.cs2rig_skin = kit.name
    started = time.time()
    assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
    shown = [obj.name for obj in importer.held_weapon_objects(armature) if obj.type == "MESH" and not obj.hide_render and "body" in obj.name]
    print(f"SKIN applied {kit.label} ({kit.name}, style {kit.style}) on {shown} in {time.time() - started:.1f} s")
    assert paint.worn_skin(armature) == kit.name and len(shown) == 1
    # Knives have one body only, whatever the finish was made for.
    assert ("legacy" in shown[0]) == skins.uses_legacy_model(kit) or len(bodies) == 1
    scene.render.filepath = str(out_dir / f"skin_{weapon_key}_s{kit.style}_{kit.name}_w{round(wear * 100):03d}.png")
    bpy.ops.render.render(write_still=True)

assert bpy.ops.cs2rig.remove_skin() == {"FINISHED"}
now = {obj.name: ([slot.material for slot in obj.material_slots], obj.hide_render) for obj in importer.held_weapon_objects(armature) if obj.type == "MESH"}
assert now == stock, "removing the finish did not restore the weapon"
assert set(bpy.data.materials) == materials_before, "a painted material was left behind"
assert not paint.worn_skin(armature)
print("SKIN OK")
