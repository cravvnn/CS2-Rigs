"""Check finishes across every weapon in a real Blender.

    blender --background --factory-startup --python tools/finish_sweep.py -- <report.json> [weapon ...]

For each weapon: reads the recipe of every finish it is offered, on the body the
finish was made for, and then applies one finish from start to finish. Slow the
first time, since every weapon is converted.
"""

import json
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import importer, paint, session  # noqa: E402
from cs2_rigs.core import finish, skins  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
report_path = Path(args[0])
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
current = session.get()
kits = list(cs2_rigs._kits.values())
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
armature = context.active_object

report = {}
partly = {}
for key in args[1:] or sorted(cs2_rigs._weapons):
    offered = [kit for kit in skins.kits_for(kits, skins.item_name(key)) if kit.name in cs2_rigs._paintable]
    if not offered:
        continue
    entry = report[key] = {"offered": len(offered), "resolved": 0, "errors": []}
    started = time.time()
    try:
        wm.cs2rig_weapon = key
        assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
        legacy, hd = paint._bodies(armature)
        materials = {}
        for body in filter(None, (legacy, hd)):
            for slot in body.material_slots:
                source = paint._game_material(slot.material)
                if source and current.material(source).get("composite_inputs"):
                    materials[body.name] = source
        entry["bodies"] = sorted(materials)
        if not materials:
            raise RuntimeError("no body takes paint")
        for kit in offered:
            body = legacy if skins.uses_legacy_model(kit) and legacy is not None else hd or legacy
            try:
                values = current.finish_values(kit, materials[body.name], 1, 0.1)
                for name in ("g_tColor", "g_tMetalness", "g_tAmbientOcclusion", "g_tMasks", "g_tWear", "g_tGrunge"):
                    if not values.get(name):
                        raise RuntimeError(f"no {name}")
                entry["resolved"] += 1
                for part in finish.not_shown(values):
                    partly.setdefault(part, set()).add(kit.name)
            except Exception as exc:  # noqa: BLE001 - every failure belongs in the report
                entry["errors"].append(f"{kit.name}: {exc}")
        wm.cs2rig_skin = offered[0].name
        assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
        assert paint.worn_skin(armature) == offered[0].name
        entry["applied"] = offered[0].name
    except Exception:  # noqa: BLE001
        entry["errors"].append(traceback.format_exc(limit=3))
    entry["seconds"] = round(time.time() - started, 1)
    print(f"FINISH {key}: {entry['resolved']}/{entry['offered']} read, applied {entry.get('applied')}, {len(entry['errors'])} errors, {entry['seconds']} s", flush=True)

failed = {key: entry["errors"] for key, entry in report.items() if entry["errors"]}
summary = {
    "weapons": len(report),
    "finish_weapon_pairs": sum(entry["offered"] for entry in report.values()),
    "read": sum(entry["resolved"] for entry in report.values()),
    "applied": sum(1 for entry in report.values() if entry.get("applied")),
    "failed_weapons": sorted(failed),
    "partly_shown": {part: len(names) for part, names in sorted(partly.items())},
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps({"summary": summary, "weapons": report}, indent=1), encoding="utf-8")
print("FINISH SUMMARY", json.dumps(summary))
print("FINISH OK" if not failed else "FINISH FAILED")
