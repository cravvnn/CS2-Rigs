"""Build a map, bring it into a scene next to an agent, and render it from above.

    blender --background --factory-startup --python tools/map_check.py -- <out_dir> [map]

The first run converts and builds the map, which takes a few minutes.
"""

import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import maps, session  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = Path(args[0])
name = args[1] if len(args) > 1 else "de_dust2"
out_dir.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
scene = context.scene
wm = context.window_manager

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
print("MAP maps:", [item[0] for item in cs2_rigs._map_items])
wm.cs2rig_map = name
library = maps.library_path(session.get(), cs2_rigs._maps[name])
started = time.time()
assert bpy.ops.cs2rig.add_map() == {"FINISHED"}
print(f"MAP added in {time.time() - started:.0f} s, library {library.stat().st_size / 1e6:.0f} MB")
assert not list(library.parent.glob("export*")), "the converted files were left behind"
assert scene.world is not None, "no sky was added"
print("MAP sky nodes:", [n.type for n in scene.world.node_tree.nodes] if scene.world.node_tree else None)
top = maps.maps_in_scene(scene)[name]
assert top.library is not None, "the map should be linked"
if not maps.probe_worlds(top):
    # A library built before indoor cubemap probes existed has none.
    assert bpy.ops.cs2rig.add_map(name=name, rebuild=True) == {"FINISHED"}
    top = maps.maps_in_scene(scene)[name]
probes = maps.probe_worlds(top)
assert probes, "the map has no indoor cubemap probes"
print("MAP probes:", [(probe["index"], probe["label"]) for probe in probes])
meshes = [o for o in top.all_objects if o.type == "MESH"]
lights = [o for o in top.all_objects if o.type == "LIGHT"]
print("MAP objects:", len(meshes), "meshes,", len(lights), "lights, collections", [c.name for c in top.children])
assert len(meshes) > 100
lights_group = next((c for c in top.children if c.name.endswith("_lights")), None)
if lights_group is not None:
    assert len(lights_group.objects) == len(lights) > 0, "the lights are not in their own collection"
world = scene.world
print("MAP sky:", world.name, "map", world.get("cs2rig_map"), "strength", world.get("cs2rig_sky_strength"))
if world.get("cs2rig_map") == name:
    environment = next((n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT"), None)
    assert environment is not None and environment.image is not None, "the real sky has no image"
    assert tuple(environment.image.size) == (environment.image.size[1] * 2, environment.image.size[1]), environment.image.size
    print("MAP sky image:", environment.image.name, tuple(environment.image.size), "packed", bool(environment.image.packed_file))

# The map's indoor cubemap probe is packed too, and can replace the sky.
assert bpy.ops.cs2rig.apply_map_probes(name=name) == {"FINISHED"}
world = scene.world
assert world.get("cs2rig_world_kind") == "probe", world.get("cs2rig_world_kind")
assert int(world.get("cs2rig_probe_index", -1)) == int(probes[0]["index"])
probe_environment = next((n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT"), None)
assert probe_environment is not None and probe_environment.image is not None, "the probe has no image"
assert tuple(probe_environment.image.size) == (probe_environment.image.size[1] * 2, probe_environment.image.size[1])
assert probe_environment.image.packed_file, "the probe image is not packed"
print("MAP probe image:", probe_environment.image.name, tuple(probe_environment.image.size))
assert bpy.ops.cs2rig.apply_map_sky(name=name) == {"FINISHED"}

corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
low = Vector(min(c[i] for c in corners) for i in range(3))
high = Vector(max(c[i] for c in corners) for i in range(3))
size = high - low
print("MAP size (m):", tuple(round(v, 1) for v in size))
assert 20 < size.x < 2000 and 20 < size.y < 2000, "the map is not at metre scale"

# An agent next to it must be human-sized in the same units.
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object
head = (armature.matrix_world @ armature.data.bones["head_0"].head_local).z
print("MAP agent head height (m):", round(head, 2))
assert 1.3 < head < 2.0

# Adding the same map twice is refused; removing it clears it out; appending makes local data.
try:
    bpy.ops.cs2rig.add_map()
except RuntimeError as exc:
    assert "already in this scene" in str(exc)
else:
    raise AssertionError("the same map was added twice")
assert bpy.ops.cs2rig.remove_map(name=name) == {"FINISHED"}
assert not maps.maps_in_scene(scene) and not [o for o in bpy.data.objects if o.library]
wm.cs2rig_map_link = False
started = time.time()
assert bpy.ops.cs2rig.add_map() == {"FINISHED"}
top = maps.maps_in_scene(scene)[name]
assert top.library is None
assert maps.probe_worlds(top), "the copied map has no cubemap probes"
assert bpy.ops.cs2rig.apply_map_probes(name=name) == {"FINISHED"}
assert scene.world.library is None and scene.world.get("cs2rig_world_kind") == "probe"
assert bpy.ops.cs2rig.apply_map_sky(name=name) == {"FINISHED"}
print(f"MAP appended in {time.time() - started:.0f} s")

# The panel builds on a worker thread; run that path with a small backdrop map and a broken one.
from cs2_rigs.core.catalog import GameMap  # noqa: E402

backdrop = next(m for m in cs2_rigs._maps.values() if m.is_backdrop and m.name != name)
for game_map, should_work in ((backdrop, True), (GameMap("no_such_map", str(Path(backdrop.package).with_name("no_such_map.vpk"))), False)):
    job = maps.BuildJob(session.get(), game_map)
    while job.running:
        time.sleep(0.5)
    print(f"MAP worker {game_map.name}: {job.elapsed}", "ok" if not job.error else job.error.splitlines()[0])
    assert bool(job.error) != should_work
    folder = maps.library_path(session.get(), game_map).parent
    assert not list(folder.glob("export*")) and not list(folder.glob("*.new.blend*"))
    assert maps.library_path(session.get(), game_map).is_file() == should_work
wm.cs2rig_map_link = True
wm.cs2rig_map = backdrop.name
assert bpy.ops.cs2rig.add_map() == {"FINISHED"} and len(maps.maps_in_scene(scene)) == 2
assert bpy.ops.cs2rig.remove_map(name=backdrop.name) == {"FINISHED"}

camera = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = max(size.x, size.y) * 1.05
camera.data.clip_end = 5000
camera.location = ((low.x + high.x) / 2, (low.y + high.y) / 2, high.z + 100)
scene.collection.objects.link(camera)
scene.camera = camera
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1400
scene.render.filepath = str(out_dir / f"map_{name}.png")
bpy.ops.render.render(write_still=True)
print("MAP OK")
