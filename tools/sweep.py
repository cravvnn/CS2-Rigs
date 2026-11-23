"""Import every agent and hold every weapon in a real Blender, and report what fails.

    blender --background --factory-startup --online-mode --python tools/sweep.py -- <report.json> [agents|weapons|all] [limit]

Agents: one skeleton, visible body meshes, the weapon bone, and the full set of
controls. Weapons: held on one agent over a matching clip, staying at the right hand.
Downloads the converter first when none is configured.
"""

import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer, rig, session  # noqa: E402
from cs2_rigs.core import catalog  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
report_path = Path(args[0])
what = args[1] if len(args) > 1 else "all"
limit = int(args[2]) if len(args) > 2 else 0

CLIP_FOR_KIND = {
    "rif": "world/rifle/_default_rifle/run_n_rifle",
    "smg": "world/rifle/_default_rifle/run_n_rifle",
    "snip": "world/rifle/_default_rifle/run_n_rifle",
    "shot": "world/rifle/_default_rifle/run_n_rifle",
    "mach": "world/rifle/_default_rifle/run_n_rifle",
    "pist": "world/pistol/_default_pistol/run_n_pistol",
    "knife": "world/knife/_default_knife/run_n_knife",
}
HAND_GAP_LIMIT = 0.12

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
scene = context.scene

if not session.converter_ready():
    print("SWEEP downloading converter")
    assert bpy.ops.cs2rig.download_converter() == {"FINISHED"}
current = session.get()
print("SWEEP converter:", current.converter.cli)
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
data = current.catalog()
agents = catalog.catalog_agents(data)
weapons = catalog.catalog_weapons(data)
report = {"converter": str(current.converter.cli), "game": current.install.version(), "agents": {}, "weapons": {}}


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def save():
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")


def check_agent(agent):
    wm.cs2rig_agent = agent.key
    if bpy.ops.cs2rig.import_agent() != {"FINISHED"}:
        return "import was cancelled"
    armature = context.active_object
    visible = [o for o in armature.children_recursive if o.type == "MESH" and not o.hide_render]
    if not visible:
        return "no visible meshes"
    if any(marker in o.name for o in visible for marker in importer.HIDDEN_PART_MARKERS):
        return "a first-person or optional part is showing"
    if not all(len(o.data.polygons) for o in visible):
        return "an empty visible mesh"
    if not any(slot.material for o in visible for slot in o.material_slots):
        return "no materials"
    if importer.WEAPON_BONE not in armature.data.bones:
        return "no weapon bone"
    if bpy.ops.cs2rig.build_rig() != {"FINISHED"}:
        return "IK controls were cancelled"
    limbs = rig.rigged_limbs(armature)
    if len(limbs) != 4:
        return f"IK on {len(limbs)} of 4 limbs"
    missing = [name for name in ("IK-roll_L", "IK-roll_R", "IK-toe_L", "IK-toe_R") if name not in armature.pose.bones]
    if missing:
        return f"no foot controls {missing}"
    twists = sum(1 for bone in armature.pose.bones for c in bone.constraints if c.name == rig.TWIST_CONSTRAINT)
    if twists != len(rig.TWISTS) * 2:
        return f"{twists} of {len(rig.TWISTS) * 2} twist bones"
    if len(rig.follows(armature)) != len(rig.FOLLOWS):
        return "a follow switch is missing"
    return None


def check_weapon(armature, weapon):
    wm.cs2rig_weapon = weapon.key
    if bpy.ops.cs2rig.attach_weapon() != {"FINISHED"}:
        return "attach was cancelled"
    meshes = [o for o in importer.held_weapon_objects(armature) if o.type == "MESH" and not o.hide_render]
    if not meshes:
        return "no visible meshes"
    if any("legacy" in o.name for o in meshes) and any("_hd" in o.name for o in meshes):
        return "both the old and the current body are showing"
    clip = CLIP_FOR_KIND.get(weapon.kind)
    if clip is None:
        return None
    wm.cs2rig_clip = clip
    if bpy.ops.cs2rig.load_clip() != {"FINISHED"}:
        return "clip was cancelled"
    start, end = (int(v) for v in armature.animation_data.action.frame_range)
    worst = 0.0
    for frame in (start, (start + end) // 2, end):
        scene.frame_set(frame)
        depsgraph = context.evaluated_depsgraph_get()
        corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.evaluated_get(depsgraph).bound_box]
        low = Vector(min(c[i] for c in corners) for i in range(3))
        high = Vector(max(c[i] for c in corners) for i in range(3))
        p = armature.matrix_world @ armature.pose.bones["hand_R"].head
        worst = max(worst, Vector(max(low[i] - p[i], 0.0, p[i] - high[i]) for i in range(3)).length)
    if worst > HAND_GAP_LIMIT:
        return f"{worst:.3f} m from the right hand"
    return None


def run(label, items, check, store):
    items = items[:limit] if limit else items
    for index, item in enumerate(items, 1):
        started = time.time()
        try:
            problem = check(item)
        except Exception as exc:  # a sweep must survive one bad asset
            problem = f"{type(exc).__name__}: {exc}"
        store[item.key] = problem or "ok"
        print(f"SWEEP {label} {index}/{len(items)} {item.key}: {problem or 'ok'} ({time.time() - started:.0f}s)", flush=True)
        save()


if what in ("agents", "all"):
    def agent_check(agent):
        try:
            return check_agent(agent)
        finally:
            clear_scene()

    run("agent", agents, agent_check, report["agents"])

if what in ("weapons", "all"):
    wm.cs2rig_agent = "ctm_sas/ctm_sas"
    assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
    holder = context.active_object
    run("weapon", weapons, lambda weapon: check_weapon(holder, weapon), report["weapons"])

for group in ("agents", "weapons"):
    results = report[group]
    failed = {k: v for k, v in results.items() if v != "ok"}
    print(f"SWEEP {group}: {len(results) - len(failed)} ok, {len(failed)} failed")
    for key, problem in failed.items():
        print(f"SWEEP   FAIL {key}: {problem}")
save()
print("SWEEP DONE")
