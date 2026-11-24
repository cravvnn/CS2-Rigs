"""Put glove finishes on agents in a real Blender and render a close-up of each hand.

    blender --background --factory-startup --python tools/glove_check.py -- <out_dir> [wear] [agent,agent] [kit,kit]

Applies each finish to each agent, at the given wear (default 0.15), and renders
the back of the left hand and the palm side of the right one at 512 px with
Eevee. Checks that each finish shows on exactly one set of gloves and that
removing it brings the default gloves back.
"""

import sys
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import gloves, importer  # noqa: E402
from cs2_rigs.core import gloves as gloves_mod  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
wear = float(args[1]) if len(args) > 1 else 0.15
agent_keys = args[2].split(",") if len(args) > 2 else ["ctm_sas/ctm_sas", "tm_balkan/tm_balkan_variantf"]
kit_names = args[3].split(",") if len(args) > 3 else ["sporty_blue_pink", "slick_snakeskin_white", "glove_sport_flames_orange"]
out_dir.mkdir(parents=True, exist_ok=True)
SIZE = 512

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
scene = context.scene

started = time.time()
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
print(f"GLOVES refresh took {time.time() - started:.1f} s")
offered = [kit for kit in cs2_rigs._kits.values() if gloves_mod.is_glove_kit(kit)]
usable = [kit for kit in offered if kit.name in cs2_rigs._paintable]
print(f"GLOVES {len(offered)} glove finishes, {len(usable)} with a recipe; panel lists {len(cs2_rigs._glove_items)}")
assert len(cs2_rigs._glove_items) == len(usable)

world = bpy.data.worlds.new("w")
scene.world = world
for node in world.node_tree.nodes:
    if node.type == "BACKGROUND":
        node.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
for index, turn in enumerate(((0.9, -0.6, -1.2), (1.1, 0.4, 1.9))):
    sun = bpy.data.objects.new(f"sun{index}", bpy.data.lights.new(f"sun{index}", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = turn
    scene.collection.objects.link(sun)
camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
camera.data.type = "ORTHO"
scene.collection.objects.link(camera)
scene.camera = camera
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = SIZE


def hand_points(mesh, left):
    """Vertices of one hand, in world space, as the skeleton deforms them."""
    evaluated = mesh.evaluated_get(context.evaluated_depsgraph_get())
    data = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world @ v.co for v in data.vertices]
    finally:
        evaluated.to_mesh_clear()
    return [p for p in points if (p.x > 0) == left]


def render_hand(mesh, left, path):
    points = hand_points(mesh, left)
    low = Vector((min(p[i] for p in points) for i in range(3)))
    high = Vector((max(p[i] for p in points) for i in range(3)))
    middle = (low + high) / 2
    camera.data.ortho_scale = max(high.x - low.x, high.y - low.y) * 1.4
    # The arms are out sideways: look down on the back of the left hand, up at the palm of the right.
    direction = Vector((0.0, 0.0, -1.0 if left else 1.0))
    camera.location = middle - direction * 2.0
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


finished = []
for agent_key in agent_keys:
    for earlier in finished:  # agents stand on the same spot, so the one before must not show
        for obj in earlier.children_recursive:
            obj.hide_render = True
    wm.cs2rig_agent = agent_key
    assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
    armature = context.active_object
    scene.frame_set(1)
    stock = gloves._default_gloves(armature)
    assert stock is not None and not stock.hide_render
    label = agent_key.split("/")[-1]
    for name in kit_names:
        kit = cs2_rigs._kits[name]
        wm.cs2rig_glove = name
        wm.cs2rig_glove_wear = wear
        wm.cs2rig_glove_seed = 7
        started = time.time()
        assert bpy.ops.cs2rig.apply_gloves() == {"FINISHED"}
        shown = [o for o in gloves._glove_meshes(armature) if not o.hide_render]
        print(f"GLOVES applied {gloves_mod.label(kit)} ({name}) on {label} in {time.time() - started:.1f} s")
        assert len(shown) == 1 and stock.hide_render and gloves.worn_gloves(armature) == name
        for other in armature.children_recursive:
            if other.type == "MESH" and other not in shown:
                other.hide_render = True
        for left in (True, False):
            render_hand(shown[0], left, out_dir / f"{label}_{name}_w{round(wear * 100):03d}_{'L' if left else 'R'}.png")
    assert bpy.ops.cs2rig.remove_gloves() == {"FINISHED"}
    assert not gloves.worn_gloves(armature) and not stock.hide_render and not stock.hide_get()
    assert not [o for o in bpy.data.objects if gloves.GLOVE_PROP in o.keys()]
    print(f"GLOVES removed on {label}")
    finished.append(armature)
print("GLOVES OK")
