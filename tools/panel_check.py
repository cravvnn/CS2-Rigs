"""Draw the panel without a window, to catch mistakes a background run cannot see.

    blender --background --factory-startup --python tools/panel_check.py

The panel is drawn through a stand-in layout that checks every icon, property and
operator it is asked for, first on an empty scene and then with an agent that has
a weapon, a finish, controls and a map.
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402

ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())


class OperatorProperties:
    """Accepts only the properties the operator really has."""

    def __init__(self, idname):
        category, name = idname.split(".")
        object.__setattr__(self, "_known", set(getattr(getattr(bpy.ops, category), name).get_rna_type().properties.keys()))
        object.__setattr__(self, "_idname", idname)

    def __setattr__(self, key, value):
        assert key in self._known, f"{self._idname} has no property '{key}'"


class Layout:
    def __init__(self):
        self.drawn = []

    def _icon(self, icon):
        assert icon in ICONS, f"unknown icon '{icon}'"

    def box(self, **_):
        return self

    def column(self, **_):
        return self

    def row(self, **_):
        return self

    def label(self, text="", icon="NONE"):
        self._icon(icon)
        self.drawn.append(text)

    def operator(self, idname, text="", icon="NONE", depress=False):
        self._icon(icon)
        self.drawn.append(text or idname)
        return OperatorProperties(idname)

    def prop(self, data, name, text="", icon="NONE", slider=False):
        self._icon(icon)
        if name.startswith('["'):
            assert name[2:-2] in data.keys(), f"no custom property {name}"
        else:
            assert hasattr(data, name), f"no property '{name}'"
            getattr(data, name)  # enum callbacks run here
        self.drawn.append(name)

    def prop_search(self, data, name, search_data, search_name, text="", icon="NONE"):
        self._icon(icon)
        assert hasattr(data, name) and hasattr(search_data, search_name)
        self.drawn.append(name)


class Panel:
    layout = None


def draw(label):
    panel = Panel()
    panel.layout = Layout()
    cs2_rigs.CS2RIG_PT_main.draw(panel, bpy.context)
    print(f"PANEL {label}: {len(panel.layout.drawn)} items")
    return panel.layout.drawn


bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
wm = bpy.context.window_manager
empty = draw("before refresh")
assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
wm.cs2rig_agent = "ctm_sas/ctm_sas"
assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
wm.cs2rig_weapon = "ak47"
assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
wm.cs2rig_skin = "cu_ak47_cobra"
assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
assert bpy.ops.cs2rig.build_rig() == {"FINISHED"}
assert bpy.ops.cs2rig.set_follow(prop="weapon_in_hand", on=True) == {"FINISHED"}
wm.cs2rig_map = "de_dust2_vanity"
assert bpy.ops.cs2rig.add_map() == {"FINISHED"}
full = draw("agent, weapon, finish, controls and map")
assert len(full) > len(empty) + 10
for expected in ("cs2rig_skin", "cs2rig.apply_skin", '["ik_arm_L"]', "Weapon Follows Right Hand", "de_dust2_vanity", "cs2rig_match_clips"):
    assert expected in full, f"the panel did not draw {expected}"

# Every state of the update row draws.
from cs2_rigs import updater  # noqa: E402
from cs2_rigs.core import update  # noqa: E402

updater.release = update.Release("9.9.9-alpha", "", "", "CS2_Rigs_9.9.9-alpha_Windows_x64.zip")
tops = {}
for state in ("available", "current", "installed", "checking", "failed", "offline"):
    updater.status = state
    tops[state] = draw(f"update {state}")[0]
assert tops["available"] == "Update Available!" and tops["current"] == "CS2-Rigs is Updated", tops
updater.status, updater.release = "offline", None

# The preferences draw too.
prefs = Panel()
prefs.layout = Layout()
for name in ("game_dir", "converter_path", "cache_dir", "log_dir"):
    setattr(prefs, name, "")
cs2_rigs.CS2RIG_Preferences.draw(prefs, bpy.context)
print("PANEL preferences:", len(prefs.layout.drawn), "items")
print("PANEL OK")
