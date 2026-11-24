"""Check every glove finish in the game in a real Blender.

    blender --background --factory-startup --python tools/glove_sweep.py -- <report.json> [--compose]

For each glove finish: reads its recipe for the left and the right glove and
checks that every texture it needs is in the game files. With --compose it also
converts those textures and builds the finish at 64 px, which takes a few
minutes the first time. Writes a report of which finishes apply and why the
others do not.
"""

import json
import sys
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import paint, session  # noqa: E402
from cs2_rigs.core import gloves  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
report_path = Path(args[0])
compose = "--compose" in args
bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
current = session.get()
kits = gloves.glove_kits(cs2_rigs._kits.values())
print(f"GLOVES {len(kits)} glove finishes in the game")

started = time.time()
listed: dict[str, set[str]] = {}


def in_package(path: str) -> bool:
    """Whether a texture named by a material is in the game files; folders are listed once."""
    path = path.removesuffix("_c") + "_c"
    folder = path.rsplit("/", 1)[0] + "/"
    if folder not in listed:
        listed[folder] = {entry.path for entry in current.converter.list_package(current.install.main_package, folder, "vtex_c")}
    return path in listed[folder]


report = {}
for kit in kits:
    entry = report[kit.name] = {"label": gloves.label(kit), "generation": None, "errors": []}
    try:
        if current.can_apply(kit) is False:
            raise RuntimeError("the game has no recipe for it")
        loaded = {}
        for left in (True, False):
            values = current.glove_values(kit, left, 5, 0.2)
            entry["generation"] = gloves.generation(values)
            names = gloves.texture_names(values)
            needed = ("g_tLayerId", "g_tTintId") if entry["generation"] == 2 else ("g_tLayerMask", "g_tSurface")
            absent = [name for name in needed if name not in names]
            if absent:
                raise RuntimeError(f"no {', '.join(absent)}")
            lost = [name for name in names if not in_package(values[name])]
            if lost:
                raise RuntimeError(f"textures not in the game files: {', '.join(f'{name} {values[name]}' for name in lost)}")
            if compose:
                for name in names:
                    if values[name] not in loaded:
                        loaded[values[name]] = paint._pixels(current.texture(values[name]))
                result = gloves.compose(values, {name: loaded[values[name]] for name in names}, 64, 0.2)
                if not (0.0 <= float(result["colour"].min()) and float(result["colour"].max()) <= 1.0):
                    raise RuntimeError("colour out of range")
    except Exception as exc:  # noqa: BLE001 - every failure belongs in the report
        entry["errors"].append(f"{exc}" if isinstance(exc, RuntimeError) else traceback.format_exc(limit=3))

applied = [name for name, entry in report.items() if not entry["errors"]]
summary = {
    "glove_finishes": len(kits),
    "apply": len(applied),
    "older_shader": sum(1 for entry in report.values() if entry["generation"] == 1 and not entry["errors"]),
    "newer_shader": sum(1 for entry in report.values() if entry["generation"] == 2 and not entry["errors"]),
    "composed": compose,
    "failed": {name: entry["errors"] for name, entry in report.items() if entry["errors"]},
    "seconds": round(time.time() - started, 1),
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps({"summary": summary, "finishes": report}, indent=1), encoding="utf-8")
print("GLOVES SUMMARY", json.dumps({key: value for key, value in summary.items() if key != "failed"}))
for name, errors in summary["failed"].items():
    print(f"GLOVES {name}: {errors[0]}")
print("GLOVES OK" if not summary["failed"] else "GLOVES FAILED")
