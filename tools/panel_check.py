"""Draw the panel without a window, to catch mistakes a background run cannot see.

    blender --background --factory-startup --python tools/panel_check.py [-- <out_dir>]

The panel is drawn through a stand-in layout that checks every icon, property and
operator it is asked for, in every state the panel can be in: empty, after the
lists are read, with a full scene, with a long job running, with a map file
missing, and in every update state. With an out_dir it also writes
panel_states.json for the HTML mock of the panel.
"""

import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402

ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())

WEAR_PROPS = {"cs2rig_wear", "cs2rig_glove_wear"}
# Blender reports the identifier of these dynamic enums, not the label the dropdown shows.
ENUM_ITEMS = {
    "cs2rig_agent": lambda: cs2_rigs._agent_items,
    "cs2rig_weapon": lambda: cs2_rigs._weapon_items,
    "cs2rig_skin": lambda: cs2_rigs._skin_enum(None, bpy.context),
    "cs2rig_glove": lambda: cs2_rigs._glove_items,
    "cs2rig_map": lambda: cs2_rigs._map_items,
}


class Node:
    """One drawn widget, or a layout that holds others."""

    def __init__(self, kind, **values):
        self.kind = kind
        self.values = values
        self.children = []

    def as_json(self):
        return {"kind": self.kind, **self.values, "children": [child.as_json() for child in self.children]}


class OperatorProperties:
    """Accepts only the properties the operator really has."""

    def __init__(self, idname):
        category, name = idname.split(".")
        object.__setattr__(self, "_known", set(getattr(getattr(bpy.ops, category), name).get_rna_type().properties.keys()))
        object.__setattr__(self, "_idname", idname)

    def __setattr__(self, key, value):
        assert key in self._known, f"{self._idname} has no property '{key}'"


class Layout:
    """Stands in for bpy.types.UILayout and records what the panel draws."""

    def __init__(self, node=None, parent=None, *, all_closed=False):
        self.node = node or Node("root")
        if parent is None:
            self.alert = False
            self.enabled = True
            self.scale_y = 1.0
            self.alignment = "EXPAND"
            self.use_property_split = False
            self.use_property_decorate = True
            self._all_closed = all_closed
        else:
            parent.node.children.append(self.node)
            self.alert = parent.alert
            self.enabled = parent.enabled
            self.scale_y = parent.scale_y
            self.alignment = parent.alignment
            self.use_property_split = parent.use_property_split
            self.use_property_decorate = parent.use_property_decorate
            self._all_closed = parent._all_closed

    def _child(self, kind, **values):
        return Layout(Node(kind, **values), self)

    def _leave(self, kind, **values):
        self.node.children.append(
            Node(kind, enabled=self.enabled, alert=self.alert, scale_y=self.scale_y, **values)
        )

    def box(self, **_):
        return self._child("box")

    def column(self, **_):
        return self._child("column")

    def row(self, **_):
        return self._child("row")

    def split(self, **_):
        return self._child("split")

    def panel(self, idname, default_closed=False):
        section = self._child("panel", idname=idname, default_closed=default_closed, closed=self._all_closed)
        header = section._child("panel_header")
        body = None if self._all_closed else section._child("panel_body")
        return header, body

    def label(self, text="", icon="NONE"):
        assert icon in ICONS, f"unknown icon '{icon}'"
        self._leave("label", text=text, icon=icon)

    @staticmethod
    def _operator_label(idname):
        category, name = idname.split(".")
        return getattr(getattr(bpy.ops, category), name).get_rna_type().name

    def operator(self, idname, text="", icon="NONE", depress=False):
        assert icon in ICONS, f"unknown icon '{icon}'"
        self._leave(
            "operator",
            idname=idname,
            text=text,
            label=text or self._operator_label(idname),
            icon=icon,
            depress=depress,
        )
        return OperatorProperties(idname)

    @staticmethod
    def _enum_label(data, name, value):
        prop = data.bl_rna.properties.get(name) if hasattr(data, "bl_rna") else None
        if prop is not None and prop.type == "ENUM":
            for item in prop.enum_items:
                if item.identifier == value:
                    return item.name
        for item in ENUM_ITEMS.get(name, lambda: [])():
            if item[0] == value:
                return item[1]
        return str(value)

    @staticmethod
    def _value_text(data, name, value):
        if name in WEAR_PROPS:
            return f"{value:.4f}"
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    def prop(self, data, name, text="", icon="NONE", slider=False):
        assert icon in ICONS, f"unknown icon '{icon}'"
        prop = None
        if name.startswith('["'):
            key = name[2:-2]
            assert key in data.keys(), f"no custom property {name}"
            value = data[key]
            prop_name = text or key
        else:
            assert hasattr(data, name), f"no property '{name}'"
            value = getattr(data, name)
            prop = data.bl_rna.properties.get(name) if hasattr(data, "bl_rna") else None
            prop_name = text or (prop.name if prop is not None else name)
        if prop is not None and prop.type == "ENUM":
            display = self._enum_label(data, name, value)
        else:
            display = self._value_text(data, name, value)
        self._leave(
            "prop",
            name=name,
            text=prop_name,
            value=value,
            display=display,
            slider=slider,
            split=self.use_property_split,
        )

    def prop_search(self, data, name, search_data, search_name, text="", icon="NONE"):
        assert icon in ICONS, f"unknown icon '{icon}'"
        assert hasattr(data, name) and hasattr(search_data, search_name)
        self._leave(
            "prop_search",
            name=name,
            text=text,
            value=getattr(data, name),
            count=len(getattr(search_data, search_name)),
        )


class Panel:
    layout = None


def panel_classes():
    return [
        cls
        for cls in cs2_rigs._classes
        if isinstance(cls, type) and issubclass(cls, bpy.types.Panel) and cls.__name__.startswith("CS2RIG_PT_")
    ]


def draw_panels(context, *, all_closed=False):
    """Draw every panel class that its poll lets show, headers included."""
    drawn = []
    for cls in panel_classes():
        visible = bool(cls.poll(context)) if hasattr(cls, "poll") else True
        entry = {"class": cls.__name__, "idname": cls.bl_idname, "label": cls.bl_label, "visible": visible}
        if visible:
            panel = Panel()
            header = Layout(all_closed=all_closed)
            panel.layout = header
            if "draw_header" in cls.__dict__:
                cls.draw_header(panel, context)
            body = Layout(all_closed=all_closed)
            panel.layout = body
            cls.draw(panel, context)
            entry["header"] = header.node.as_json()["children"]
            entry["body"] = body.node.as_json()["children"]
        drawn.append(entry)
    return drawn


STATES = []


def _node_texts(nodes):
    out = []
    for node in nodes:
        if node.get("kind") in ("label", "operator", "prop", "prop_search"):
            text = node.get("text") or node.get("label")
            if text:
                out.append(text)
        if node.get("children"):
            out.extend(_node_texts(node["children"]))
    return out


def draw(label, context, *, all_closed=False):
    panels = draw_panels(context, all_closed=all_closed)
    states = [panel for panel in panels if panel["visible"]]
    texts = []
    names = []
    operators = []
    sections = {}

    def collect(nodes):
        for node in nodes:
            if node.get("kind") == "panel":
                hdr = next((c for c in node["children"] if c["kind"] == "panel_header"), None)
                sections[node["idname"]] = {
                    "idname": node["idname"],
                    "default_closed": node["default_closed"],
                    "closed": node["closed"],
                    "header_texts": _node_texts(hdr["children"]) if hdr else [],
                }
            if node.get("kind") in ("label", "operator", "prop", "prop_search"):
                text = node.get("text") or node.get("label")
                if text:
                    texts.append(text)
            if node.get("kind") in ("prop", "prop_search"):
                names.append(node["name"])
            if node.get("kind") == "operator":
                operators.append(node["idname"])
            if node.get("children"):
                collect(node["children"])

    for panel in states:
        collect(panel.get("header", []))
        collect(panel.get("body", []))
    entries = {
        "state": label,
        "panels": panels,
        "sections": sections,
        "texts": texts,
        "names": names,
        "operators": operators,
    }
    STATES.append(entries)
    print(f"PANEL {label}: {len(sections)} sections, {len(texts)} items")
    return entries


def find(state, idname):
    return next(panel for panel in state["panels"] if panel["idname"] == idname)


def visible_sections(state):
    return [sec["header_texts"][0] for sec in state["sections"].values() if sec["header_texts"]]


def draw_full(context):
    wm = context.window_manager
    wm.cs2rig_agent = "ctm_diver/ctm_diver_varianta"
    assert bpy.ops.cs2rig.import_agent() == {"FINISHED"}
    agent_only = draw("agent, no weapon yet", context)
    assert "Next: choose a weapon and press Hold Weapon." in agent_only["texts"]
    assert "cs2rig.build_rig" in agent_only["operators"]
    assert {"Agent", "Weapon", "Gloves", "Animation", "Pose Controls", "Map", "Tools"} <= set(visible_sections(agent_only))
    wm.cs2rig_weapon = "ak47"
    assert bpy.ops.cs2rig.attach_weapon() == {"FINISHED"}
    wm.cs2rig_skin = "gs_ak47_bloodsport"
    wm.cs2rig_wear, wm.cs2rig_seed = 0.15, 42
    assert bpy.ops.cs2rig.apply_skin() == {"FINISHED"}
    wm.cs2rig_glove = "slick_red"
    wm.cs2rig_glove_wear, wm.cs2rig_glove_seed = 0.15, 44
    assert bpy.ops.cs2rig.apply_gloves() == {"FINISHED"}
    assert bpy.ops.cs2rig.build_rig() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    object_mode = draw("pose controls, Object Mode", context)
    assert "cs2rig.pose_mode" in object_mode["operators"]
    assert "Pose Mode shows the controls." in object_mode["texts"]
    bpy.ops.object.mode_set(mode="POSE")
    assert bpy.ops.cs2rig.set_follow(prop="weapon_in_hand", on=True) == {"FINISHED"}
    wm.cs2rig_map = "de_dust2_vanity"
    assert bpy.ops.cs2rig.add_map() == {"FINISHED"}
    wm.cs2rig_clip = "world/rifle/_default_rifle/run_n_rifle"
    assert bpy.ops.cs2rig.load_clip() == {"FINISHED"}


class FakeJob:
    """A job object shaped like the real one, for drawing the progress rows."""

    def __init__(self, **values):
        self.__dict__.update(values)
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_dir = Path(args[0]) if args else None

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
context = bpy.context
wm = context.window_manager

PANELS = panel_classes()
assert len(PANELS) == 1, [cls.bl_idname for cls in PANELS]
assert PANELS[0].bl_idname == "CS2RIG_PT_main", PANELS[0].bl_idname
assert PANELS[0].bl_space_type == "VIEW_3D" and PANELS[0].bl_region_type == "UI"

LABELS = {
    cs2_rigs.CS2RIG_OT_refresh: "Read Game Files",
    cs2_rigs.CS2RIG_OT_download_converter: "Download Converter (65 MB)",
    cs2_rigs.CS2RIG_OT_show_preferences: "Set Game Folder...",
    cs2_rigs.CS2RIG_OT_fix_eyes: "Rebuild Eye Materials",
    cs2_rigs.CS2RIG_OT_build_clip_previews: "Build Clip Thumbnails",
    cs2_rigs.CS2RIG_OT_build_rig: "Add Pose Controls",
    cs2_rigs.CS2RIG_OT_remove_rig: "Remove Pose Controls",
}
for operator, label in LABELS.items():
    assert operator.bl_label == label, (operator.bl_idname, operator.bl_label)

PROPERTY_NAMES = {
    "cs2rig_agent": "Agent",
    "cs2rig_weapon": "Weapon",
    "cs2rig_skin": "Finish",
    "cs2rig_wear": "Weapon Wear",
    "cs2rig_seed": "Weapon Seed",
    "cs2rig_glove": "Gloves",
    "cs2rig_glove_wear": "Glove Wear",
    "cs2rig_glove_seed": "Glove Seed",
    "cs2rig_map": "Map",
    "cs2rig_map_link": "Link to Map File",
    "cs2rig_clip": "Clip",
}
for name, label in PROPERTY_NAMES.items():
    assert bpy.types.WindowManager.bl_rna.properties[name].name == label, (name, bpy.types.WindowManager.bl_rna.properties[name].name)

from types import SimpleNamespace  # noqa: E402

assert cs2_rigs.CS2RIG_OT_snap_limb.description(None, SimpleNamespace(limb="leg", side="R", to_ik=False)) == (
    "Return the right leg to the game clip without moving it"
)

from cs2_rigs import updater  # noqa: E402
from cs2_rigs.core import update  # noqa: E402

updater.release = update.Release("9.9.9-alpha", "", "", "CS2_Rigs_9.9.9-alpha_Windows_x64.zip")
updater.status = "current"

empty = draw("empty, before reading the game files", context)
assert visible_sections(empty) == [], visible_sections(empty)
assert "Read Game Files" in empty["texts"]
assert "Next: read the game files, then import an agent." in empty["texts"]

assert bpy.ops.cs2rig.refresh() == {"FINISHED"}
no_agent = draw("after reading, no agent", context)
assert "Agent" in visible_sections(no_agent) and "Weapon" not in visible_sections(no_agent), visible_sections(no_agent)
assert {"Agent", "Map", "Tools"} <= set(visible_sections(no_agent)), visible_sections(no_agent)
assert "Next: choose an agent and press Import Agent." in no_agent["texts"]

draw_full(context)
full = draw("agent, weapon, finish, gloves, controls and map", context)
assert {"Agent", "Weapon", "Gloves", "Animation", "Pose Controls", "Map", "Tools"} == set(visible_sections(full)), visible_sections(full)
# Open headers show only the section title (no summary clutter).
for sec_id, expected_title in (
    ("cs2rig_agent", "Agent"),
    ("cs2rig_weapon", "Weapon"),
    ("cs2rig_gloves", "Gloves"),
    ("cs2rig_animation", "Animation"),
    ("cs2rig_pose", "Pose Controls"),
    ("cs2rig_map", "Map"),
    ("cs2rig_tools", "Tools"),
):
    assert full["sections"][sec_id]["header_texts"] == [expected_title], full["sections"][sec_id]
for expected in (
    "cs2rig_skin",
    "cs2rig_wear",
    "cs2rig_seed",
    "cs2rig_glove",
    "cs2rig_glove_wear",
    "cs2rig_glove_seed",
    "cs2rig_match_clips",
    '["ik_arm_L"]',
):
    assert expected in full["names"], f"the panel did not draw {expected}"
for expected in (
    "cs2rig.attach_weapon",
    "cs2rig.apply_skin",
    "cs2rig.apply_gloves",
    "cs2rig.load_clip",
    "cs2rig.snap_limb",
    "cs2rig.set_follow",
    "cs2rig.remove_rig",
    "cs2rig.add_map",
    "cs2rig.remove_map",
    "cs2rig.build_clip_previews",
    "cs2rig.fix_eyes",
):
    assert expected in full["operators"], f"the panel did not draw {expected}"
for expected in (
    "Weapon Follows Right Hand",
    "Weapon Wear",
    "Weapon Seed",
    "Glove Wear",
    "Glove Seed",
    "de_dust2_vanity · linked",
    "Applied: Bloodsport · wear 0.15 · seed 42",
    "Wearing: Driver Gloves | Crimson Weave · wear 0.15 · seed 44",
    "Only clips for AK-47",
    "Clip thumbnails: 1,795 of 1,795",
):
    assert expected in full["texts"], f"the panel did not draw {expected}"

# Collapsed headers show the section title on the left and the dimmed summary on the right.
closed = draw("all sections closed", context, all_closed=True)
assert closed["sections"]["cs2rig_agent"]["header_texts"] == ["Agent", "Cmdr. Davida 'Goggles' Fernandez (CT)"]
assert closed["sections"]["cs2rig_weapon"]["header_texts"] == ["Weapon", "AK-47 · Bloodsport"]
assert closed["sections"]["cs2rig_gloves"]["header_texts"] == ["Gloves", "Driver Gloves | Crimson Weave"]
assert closed["sections"]["cs2rig_animation"]["header_texts"] == ["Animation", "run_n_rifle"]
assert closed["sections"]["cs2rig_pose"]["header_texts"] == ["Pose Controls", "On"]
assert closed["sections"]["cs2rig_map"]["header_texts"] == ["Map", "de_dust2_vanity"]
assert closed["sections"]["cs2rig_tools"]["header_texts"] == ["Tools"]

# A map whose built file came from older game files gets a Rebuild button.
real_newer = cs2_rigs.maps.newer_build_possible
cs2_rigs.maps.newer_build_possible = lambda session, game_map: True
newer = draw("a newer map build is possible", context)
assert "Rebuild de_dust2_vanity" in newer["texts"], newer["texts"]
cs2_rigs.maps.newer_build_possible = real_newer

# A failed step stays at the top, with the log beside it.
wm.cs2rig_error = "The converter failed: exit code 1"
failed = draw("last step failed", context)
assert "The converter failed: exit code 1" in failed["texts"], failed["texts"]
assert "cs2rig.open_log_folder" in failed["operators"], failed["operators"]
wm.cs2rig_error = ""

# A long job running in every place that has one.
real_jobs = (cs2_rigs._map_job, cs2_rigs._preview_job, cs2_rigs._glove_job, cs2_rigs._finish_job)
cs2_rigs._map_job = FakeJob(running=True, elapsed="2:14", game_map=FakeJob(name="de_dust2_vanity"))
cs2_rigs._preview_job = FakeJob(running=True, progress="Loading clips 12/1795  0:42")
cs2_rigs._glove_job = FakeJob(stage="Composing the finish", busy=True)
cs2_rigs._finish_job = FakeJob(stage="Applying the finish", busy=True)
running = draw("long jobs running", context)
assert running["texts"].count("Cancel") == 4, running["texts"]
assert "Building de_dust2_vanity  2:14" in running["texts"]
for idname, job in (
    ("cancel_map_build", cs2_rigs._map_job),
    ("cancel_clip_previews", cs2_rigs._preview_job),
    ("cancel_gloves", cs2_rigs._glove_job),
    ("cancel_finish", cs2_rigs._finish_job),
):
    assert getattr(bpy.ops.cs2rig, idname)() == {"FINISHED"}, idname
    assert job.cancelled, idname
cs2_rigs._map_job, cs2_rigs._preview_job, cs2_rigs._glove_job, cs2_rigs._finish_job = real_jobs

# A map whose linked file has gone.
libraries = [library for library in bpy.data.libraries if library.filepath]
assert libraries, "the full state should have linked the map"
library = libraries[0]
saved = library.filepath
library.filepath = str(out_dir / "fixture" / "maps" / "de_missing" / "de_missing.blend") if out_dir else "X:/maps/de_missing/de_missing.blend"
missing = draw("a linked map file is missing", context)
assert any("de_missing: file missing" in text for text in missing["texts"]), missing["texts"]
library.filepath = saved

# Every state of the update row, and it stays the first thing drawn.
for state in ("idle", "offline", "checking", "available", "current", "failed", "installed"):
    updater.status = state
    drawn = draw(f"update {state}", context)
    main = find(drawn, "CS2RIG_PT_main")
    first = main["body"][0]
    if first["kind"] in ("row", "column", "box"):
        first = first["children"][0]
    assert first["kind"] in ("label", "operator"), first
    if state == "available":
        assert first["text"] == "Update Available!", first
    if state == "current":
        assert first["text"] == "CS2-Rigs is Updated", first
updater.status, updater.release = "offline", None

# The preferences draw too.
class Preferences:
    layout = None


prefs = Preferences()
prefs.layout = Layout()
for name in ("game_dir", "converter_path", "cache_dir", "log_dir"):
    setattr(prefs, name, "")
cs2_rigs.CS2RIG_Preferences.draw(prefs, context)
print("PANEL preferences:", len(prefs.layout.node.children), "items")

if out_dir is not None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "panel_states.json").write_text(json.dumps(STATES, indent=1), encoding="utf-8")
    print("PANEL states written to", out_dir / "panel_states.json")

print("PANEL OK")
