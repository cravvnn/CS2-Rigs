"""CS2 Rigs: Counter-Strike 2 agents, clips and maps for Blender, built from your install."""

from __future__ import annotations

import atexit
import functools
import platform
import shutil
import time
import tomllib
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from . import clip_browser, clip_previews, eyes, gloves, hdri, importer, maps, paint, rig, session, stickers, studio, updater, viewmodel
from .core import cache as cache_mod
from .core import clipdata as clipdata_mod
from .core import hdri as hdri_core
from .core import log as log_mod
from .core import catalog as catalog_mod
from .core import gloves as gloves_mod
from .core import proc
from .core import skins as skins_mod
from .core import stickers as stickers_mod
from .core import studio as studio_core
from .core import viewmodel as vm_core
from .core.download import CLI_VERSION
from .map_build import MAP_NAME_PROP, PROBE_INDEX_PROP, PROBE_LABEL_PROP, WORLD_KIND_PROP

# Filled by Read Game Files. Blender's enum callbacks need the item lists kept alive here.
_agent_items: list[tuple[str, str, str]] = []
_agents: dict[str, catalog_mod.Agent] = {}
_agent_shorts: dict[str, str] = {}
_weapon_items: list[tuple[str, str, str]] = []
_map_items: list[tuple[str, str, str]] = []
_weapons: dict[str, catalog_mod.Weapon] = {}
_maps: dict[str, catalog_mod.GameMap] = {}
_clip_labels: list[str] = []
_kits: dict[str, skins_mod.PaintKit] = {}
_paintable: set[str] = set()  # kits whose recipe is in the game files
_skin_items: dict[str, list[tuple[str, str, str]]] = {}  # per weapon key
_glove_items: list[tuple[str, str, str]] = []
_stickers_by_search: dict[str, stickers_mod.StickerKit] = {}
_stickers_by_material: dict[str, stickers_mod.StickerKit] = {}
_sticker_search_by_material: dict[str, str] = {}
_hdri_items: list[tuple[str, str, str]] = []
_hdri_skies: dict[str, hdri_core.Skybox] = {}
_map_job: maps.BuildJob | None = None
_preview_job: clip_previews.PreviewJob | None = None
_glove_job: gloves.GloveJob | None = None
_finish_job: paint.FinishJob | None = None
_exit_hooked = False
_syncing_studio = False
_syncing_pov = False
_syncing_sticker = False
_syncing_hdri = False
_sticker_slot_items: list[tuple[str, str, str]] = [
    (str(i), f"Slot {i + 1}", f"Weapon sticker slot {i + 1}")
    for i in range(stickers_mod.MAX_SLOTS)
]
_studio_preset_items: list[tuple[str, str, str]] = [
    (studio_core.PRESETS[key].key, studio_core.PRESETS[key].label, studio_core.PRESETS[key].description)
    for key in studio_core.PRESET_ORDER
]
_pov_preset_items: list[tuple[str, str, str]] = [
    (vm_core.PRESETS[key].key, vm_core.PRESETS[key].label, vm_core.PRESETS[key].description)
    for key in vm_core.PRESET_ORDER
]
_probe_enum_items: list[tuple[str, str, str]] = []


def _on_studio_setting_changed(self, context) -> None:
    """Update the studio lighting and backdrop live when the user tweaks a Studio setting."""
    if _syncing_studio or context is None or context.scene is None:
        return
    if studio.has_studio(context.scene):
        studio.apply_studio_look(context, self.cs2rig_studio_preset, self.cs2rig_studio_backdrop)


def _on_hdri_strength_changed(self, context) -> None:
    """Drive the active CS2 sky's strength live, on a standalone HDRI, map sky or cubemap probe."""
    if _syncing_hdri or context is None or context.scene is None:
        return
    if hdri.has_hdri(context.scene):
        hdri.set_strength(context.scene, self.cs2rig_hdri_strength)


def _on_hdri_rotation_changed(self, context) -> None:
    """Drive the active CS2 sky's rotation live, on a standalone HDRI, map sky or cubemap probe."""
    if _syncing_hdri or context is None or context.scene is None:
        return
    if hdri.has_hdri(context.scene):
        hdri.set_rotation(context.scene, self.cs2rig_hdri_rotation)


def _hdri_enum(self, context):
    return _hdri_items or [("NONE", "No CS2 skies", "Read the game files to list the game's skies")]


def _sync_sticker_controls(context) -> None:
    """Sync the Stickers controls to the currently selected slot on the active weapon."""
    global _syncing_sticker
    if context is None:
        return
    wm = context.window_manager
    target = importer.find_weapon_target(context)
    slot = int(wm.cs2rig_sticker_slot)
    applied = stickers.slot_sticker(target, slot) if target is not None else None
    _syncing_sticker = True
    try:
        if applied is not None:
            search_name = _sticker_search_by_material.get(applied.material, applied.label or applied.material)
            wm.cs2rig_sticker = search_name
            wm.cs2rig_sticker_wear = applied.wear
            off_x, off_y, scale, rot_deg = (
                applied.offset[0] if applied.offset is not None else 0.0,
                applied.offset[1] if applied.offset is not None else -0.4,
                applied.scale if applied.scale is not None else 14.1,
                applied.rotation_deg if applied.rotation_deg is not None else 0.0,
            )
            body, _kind = stickers._active_body(target) if target is not None else (None, "current")
            if body is not None:
                for ms in body.material_slots:
                    mat = ms.material
                    if mat is None or mat.node_tree is None:
                        continue
                    node = next(
                        (n for n in mat.node_tree.nodes if n.type == "GROUP" and n.get(stickers.STICKER_SLOT_PROP) == slot),
                        None,
                    )
                    if node is not None:
                        off_x = float(node.inputs["Offset X"].default_value)
                        off_y = float(node.inputs["Offset Y"].default_value)
                        scale = float(node.inputs["Scale"].default_value)
                        rot_deg = float(node.inputs["Rotation Turns"].default_value) * 360.0
                        break
            wm.cs2rig_sticker_offset_x = off_x
            wm.cs2rig_sticker_offset_y = off_y
            wm.cs2rig_sticker_scale = scale
            wm.cs2rig_sticker_rotation = rot_deg
        else:
            wm.cs2rig_sticker_wear = 0.0
            wm.cs2rig_sticker_offset_x = 0.0
            wm.cs2rig_sticker_offset_y = -0.4
            wm.cs2rig_sticker_scale = 14.1
            wm.cs2rig_sticker_rotation = 0.0
    finally:
        _syncing_sticker = False


def _on_sticker_slot_changed(self, context) -> None:
    if not _syncing_sticker:
        _sync_sticker_controls(context)


def _on_sticker_param_changed(self, context) -> None:
    """Update the active sticker slot's shader inputs live (<1 ms) when sliders change."""
    if _syncing_sticker or context is None:
        return
    target = importer.find_weapon_target(context)
    if target is None:
        return
    slot = int(self.cs2rig_sticker_slot)
    if stickers.slot_sticker(target, slot) is None:
        return
    stickers.update_slot_live(
        target,
        slot,
        wear=self.cs2rig_sticker_wear,
        offset=(self.cs2rig_sticker_offset_x, self.cs2rig_sticker_offset_y),
        scale=self.cs2rig_sticker_scale,
        rotation_deg=self.cs2rig_sticker_rotation,
    )


def _on_pov_preset_changed(self, context) -> None:
    """Apply a Classic or Desktop viewmodel preset live to the active first-person rig."""
    global _syncing_pov
    if _syncing_pov or context is None or context.scene is None:
        return
    preset = self.cs2rig_pov_preset
    if preset in ("classic", "desktop"):
        p = vm_core.get_preset(preset)
        _syncing_pov = True
        try:
            self.cs2rig_pov_fov = p.fov
            self.cs2rig_pov_offset_x, self.cs2rig_pov_offset_y, self.cs2rig_pov_offset_z = p.offset
        finally:
            _syncing_pov = False
    armature = importer.find_viewmodel_arms(context)
    if armature is not None:
        viewmodel.apply_pov_settings(
            armature,
            preset,
            self.cs2rig_pov_fov,
            self.cs2rig_pov_offset_x,
            self.cs2rig_pov_offset_y,
            self.cs2rig_pov_offset_z,
        )


def _on_pov_slider_changed(self, context) -> None:
    """Update POV camera FOV and arms offset live when the user adjusts a slider."""
    global _syncing_pov
    if _syncing_pov or context is None or context.scene is None:
        return
    detected = vm_core.detect_preset(
        self.cs2rig_pov_fov,
        self.cs2rig_pov_offset_x,
        self.cs2rig_pov_offset_y,
        self.cs2rig_pov_offset_z,
    )
    if self.cs2rig_pov_preset != detected:
        _syncing_pov = True
        try:
            self.cs2rig_pov_preset = detected
        finally:
            _syncing_pov = False
    armature = importer.find_viewmodel_arms(context)
    if armature is not None:
        viewmodel.apply_pov_settings(
            armature,
            detected,
            self.cs2rig_pov_fov,
            self.cs2rig_pov_offset_x,
            self.cs2rig_pov_offset_y,
            self.cs2rig_pov_offset_z,
        )


def _cancel_jobs() -> None:
    """Stop every running job, so no converter or background Blender outlives Blender."""
    for job in (_map_job, _preview_job, _glove_job, _finish_job):
        if job is not None:
            job.cancel()
    proc.kill_all()


def _remember_error(context, message: str) -> None:
    """Keep the last failure in the panel, where it stays readable after the status bar clears."""
    wm = getattr(context, "window_manager", None)
    if wm is not None and hasattr(wm, "cs2rig_error"):
        wm.cs2rig_error = message.splitlines()[0]


def _clear_error(context) -> None:
    wm = getattr(context, "window_manager", None)
    if wm is not None and getattr(wm, "cs2rig_error", ""):
        wm.cs2rig_error = ""


def _report_error(operator, context, message: str) -> None:
    """Report an error in Blender and keep it in the panel until the next successful step."""
    _remember_error(context, message)
    operator.report({"ERROR"}, message)


_PRESS_REFRESH = [("NONE", "Read Game Files first", "Read the lists from the game first")]


def _reports_errors(execute):
    """Show a failed step as an error in Blender and in the panel, instead of a traceback, and write each run to the log."""

    @functools.wraps(execute)
    def wrapper(self, context):
        started = time.monotonic()
        try:
            result = execute(self, context)
        except (RuntimeError, OSError) as exc:  # setup, converter, download, import and file problems
            log_mod.logger.error("%s failed: %s (%s)", _op_name(self), exc, _selection(context, _op_name(self)))
            _report_error(self, context, str(exc))
            return {"CANCELLED"}
        except Exception:
            log_mod.logger.exception("%s stopped with an unexpected error (%s)", _op_name(self), _selection(context, _op_name(self)))
            _remember_error(context, f"{_op_name(self)} stopped with an unexpected error.")
            raise
        log_mod.logger.info("%s %s in %.1f s", _op_name(self), ", ".join(sorted(result)).lower(), time.monotonic() - started)
        _clear_error(context)
        return result

    return wrapper


def _op_name(operator) -> str:
    """cs2rig.apply_skin rather than the CS2RIG_OT_apply_skin Blender stores once registered."""
    prefix, _, name = operator.bl_idname.partition("_OT_")
    return f"{prefix.lower()}.{name}" if name else operator.bl_idname


def _selection(context, operator) -> str:
    """What was chosen in the panel for this step, for the log."""
    wm = context.window_manager
    names = ("map",) if "map" in operator else ("agent", "weapon", "skin", "glove", "clip")
    chosen = []
    for name in names:
        value = getattr(wm, f"cs2rig_{name}", "")
        if value and value != "NONE":
            chosen.append(f"{name} {value}")
            if name == "skin":
                chosen.append(f"wear {wm.cs2rig_wear:.2f} seed {wm.cs2rig_seed}")
    return ", ".join(chosen) or "nothing chosen"


def _addon_version() -> str:
    try:
        with open(Path(__file__).with_name("blender_manifest.toml"), "rb") as handle:
            return tomllib.load(handle).get("version", "?")
    except (OSError, tomllib.TOMLDecodeError):
        return "?"


def _log_dir_setting() -> Path | None:
    addon = bpy.context.preferences.addons.get(__package__)
    value = addon.preferences.log_dir if addon else ""
    return Path(bpy.path.abspath(value)) if value else None


def _start_log() -> None:
    log_mod.configure(_log_dir_setting())
    log_mod.logger.info(
        "CS2 Rigs %s, Blender %s, %s %s", _addon_version(), bpy.app.version_string, platform.system(), platform.release()
    )


def _building() -> maps.BuildJob | None:
    """The map build in progress. A finished job whose operator went away (a file was loaded) does not count."""
    return _map_job if _map_job is not None and _map_job.running else None


def _held_weapon(armature: bpy.types.Object | None) -> catalog_mod.Weapon | None:
    return _weapons.get(importer.held_weapon_key(armature)) if armature else None


def _active_weapon(target: bpy.types.Object | None) -> catalog_mod.Weapon | None:
    return _weapons.get(importer.weapon_key(target)) if target is not None else None


def _skin_enum(self, context):
    """Finishes for the active weapon (held by an agent or standalone)."""
    target = importer.find_weapon_target(context)
    key = importer.weapon_key(target) if target is not None else ""
    if key not in _skin_items:
        offered = skins_mod.kits_for(list(_kits.values()), skins_mod.item_name(key))
        _skin_items[key] = [
            (kit.name, kit.label, f"{kit.rarity.title()} finish" if kit.rarity else "Finish")
            for kit in offered
            if kit.name in _paintable
        ]
    return _skin_items[key] or [("NONE", "No finishes", "This weapon has no finish that can be shown")]


def _glove_enum(self, context):
    return _glove_items or [("NONE", "No gloves", "Read the game files to list the glove finishes")]


def _fill_clips(context: bpy.types.Context) -> None:
    """List the clips to choose from: viewmodel clips for a first-person rig, world clips for a third-person agent."""
    wm = context.window_manager
    clips = wm.cs2rig_clips
    clips.clear()
    rig_target = importer.find_character_rig(context)
    if importer.is_viewmodel_arms(rig_target):
        paths = [catalog_mod.CLIP_PREFIX + label for label in _clip_labels]
        weapon = _held_weapon(rig_target) if wm.cs2rig_match_clips else None
        labels = [catalog_mod.clip_label(path) for path in vm_core.clips_for_fp_weapon(paths, weapon)]
    elif wm.cs2rig_match_clips:
        paths = [catalog_mod.CLIP_PREFIX + label for label in _clip_labels]
        weapon = _held_weapon(rig_target)
        labels = [catalog_mod.clip_label(path) for path in catalog_mod.clips_for_weapon(paths, weapon)]
    else:
        labels = _clip_labels
    for label in labels:
        clips.add().name = label
    if wm.cs2rig_clip and wm.cs2rig_clip not in clips:
        wm.cs2rig_clip = ""  # hidden by the filter, so Load Clip must not load it


def _fill_lists(context: bpy.types.Context, current: session.Session) -> None:
    """Read the agent, weapon, finish, glove, clip and map lists. Instant once the game files have been read."""
    data = current.catalog()
    agents = catalog_mod.catalog_agents(data)
    _agents.clear()
    _agents.update({agent.key: agent for agent in agents})
    _agent_shorts.clear()
    _agent_shorts.update(zip((agent.key for agent in agents), catalog_mod.short_names([a.name or a.label for a in agents])))
    _agent_items[:] = [
        (agent.key, f"{agent.side}  {_agent_shorts[agent.key]}", agent.name or f"{agent.side} agent") for agent in agents
    ]
    _weapons.clear()
    _weapons.update({weapon.key: weapon for weapon in catalog_mod.catalog_weapons(data)})
    _weapon_items[:] = [
        (weapon.key, weapon.name or weapon.label, _KIND_LABELS.get(weapon.kind, "Weapon")) for weapon in _weapons.values()
    ]
    _maps.clear()
    _maps.update({game_map.name: game_map for game_map in catalog_mod.maps_in(current.install.maps_dir)})
    _map_items[:] = [
        (m.name, f"{m.name}  (backdrop)" if m.is_backdrop else m.name, "Backdrop scene" if m.is_backdrop else "Playable map")
        for m in _maps.values()
    ]
    _clip_labels[:] = [catalog_mod.clip_label(clip) for clip in sorted(data.get("clips", []), key=catalog_mod.clip_order)]
    _kits.clear()
    _kits.update({kit.name: kit for kit in current.skins()})
    _paintable.clear()
    _paintable.update(kit.name for kit in _kits.values() if current.can_apply(kit))
    _skin_items.clear()
    _glove_items[:] = [
        (kit.name, gloves_mod.label(kit), f"{kit.rarity.title()} finish" if kit.rarity else "Glove finish")
        for kit in gloves_mod.glove_kits(_kits.values())
        if kit.name in _paintable
    ]
    _fill_stickers(context, current.stickers())
    _fill_hdri(current)
    _fill_clips(context)


def _fill_hdri(current: session.Session) -> None:
    """List the game's skies. The catalogue is built once and kept in the cache."""
    try:
        skies = hdri_core.available(current.cache.hdri_dir, current.converter, current.install.main_package)
    except (RuntimeError, OSError) as exc:  # the rest of the panel still works without skies
        log_mod.logger.warning("listing the CS2 skies failed: %s", exc)
        return
    _hdri_skies.clear()
    _hdri_skies.update({sky.key: sky for sky in skies})
    _hdri_items[:] = [
        (sky.key, sky.label, sky.material + (f", the sky {sky.map} uses" if sky.map else ""))
        for sky in skies
    ]


def _fill_stickers(context: bpy.types.Context, sticker_list: list[stickers_mod.StickerKit]) -> None:
    """Populate the searchable sticker collection for prop_search."""
    wm = context.window_manager
    coll = wm.cs2rig_stickers
    coll.clear()
    _stickers_by_search.clear()
    _stickers_by_material.clear()
    _sticker_search_by_material.clear()

    label_counts: dict[str, int] = {}
    for kit in sticker_list:
        label_counts[kit.label] = label_counts.get(kit.label, 0) + 1

    for kit in sticker_list:
        display = f"{kit.label} [{kit.id}]" if label_counts[kit.label] > 1 else kit.label
        _stickers_by_search[display] = kit
        _stickers_by_material[kit.material] = kit
        _sticker_search_by_material[kit.material] = display
        coll.add().name = display


def _agent_short(key: str) -> str:
    """The agent's name as it fits a dropdown."""
    agent = _agents.get(key)
    return _agent_shorts.get(key) or (agent.label if agent is not None else key.rsplit("/", 1)[-1])


def _agent_title(armature: bpy.types.Object) -> str:
    """'B Squadron Officer (CT)', for the main panel and the Agent header."""
    key = armature.get(importer.AGENT_KEY_PROP, "")
    agent = _agents.get(key)
    if agent is None:
        return armature.name.removesuffix(".skeleton")
    return f"{_agent_short(key)} ({agent.side})" if agent.side != "Other" else _agent_short(key)


def _weapon_title(weapon: catalog_mod.Weapon | None) -> str:
    if weapon is None:
        return "the held weapon"
    return weapon.name or weapon.label


def _standalone_title(root: bpy.types.Object) -> str:
    weapon = _active_weapon(root)
    if weapon is not None:
        return weapon.name or weapon.label
    return root.name.removesuffix(".weapon")


def _choose(wm, name: str, value, items) -> None:
    """Point a dropdown at `value` when it is one of its items."""
    if value and value in {item[0] for item in items}:
        setattr(wm, name, value)


def _sync_hdri_controls(context: bpy.types.Context) -> None:
    """Point the HDRI dropdown and sliders at the CS2 sky the scene is using."""
    global _syncing_hdri
    if context is None or not hdri.has_hdri(context.scene):
        return
    wm = context.window_manager
    _syncing_hdri = True
    try:
        _choose(wm, "cs2rig_hdri", hdri.key(context.scene.world), _hdri_items)
        wm.cs2rig_hdri_strength = hdri.strength(context.scene)
        wm.cs2rig_hdri_rotation = hdri.rotation_offset(context.scene)
    finally:
        _syncing_hdri = False


def _sync_to_scene(context: bpy.types.Context) -> None:
    """Point the panel at what the scene has: studio settings, HDRI, agent/FP rig, weapon, finish, gloves and clip."""
    global _syncing_studio, _syncing_pov
    wm = context.window_manager
    if studio.has_studio(context.scene):
        _syncing_studio = True
        try:
            _choose(wm, "cs2rig_studio_preset", studio.studio_preset(context.scene), _studio_preset_items)
            wm.cs2rig_studio_backdrop = studio.studio_backdrop_enabled(context.scene)
        finally:
            _syncing_studio = False
    if hdri.has_hdri(context.scene):
        _sync_hdri_controls(context)
    armature = importer.find_character_rig(context)
    if armature is not None:
        _choose(wm, "cs2rig_agent", armature.get(importer.AGENT_KEY_PROP), _agent_items)
        _fill_clips(context)  # the clips depend on the rig type and held weapon
        if importer.is_viewmodel_arms(armature):
            preset, fov, ox, oy, oz = viewmodel.read_pov_settings(armature)
            _syncing_pov = True
            try:
                _choose(wm, "cs2rig_pov_preset", preset, _pov_preset_items)
                wm.cs2rig_pov_fov = fov
                wm.cs2rig_pov_offset_x, wm.cs2rig_pov_offset_y, wm.cs2rig_pov_offset_z = ox, oy, oz
            finally:
                _syncing_pov = False
        glove = gloves.worn_glove_settings(armature)
        if glove is not None:
            _choose(wm, "cs2rig_glove", glove[0], _glove_items)
            wm.cs2rig_glove_wear, wm.cs2rig_glove_seed = glove[1], glove[2]
        action = armature.animation_data.action if armature.animation_data else None
        clip = action.get(importer.CLIP_KEY_PROP) if action is not None else None
        if clip and catalog_mod.clip_label(clip) in wm.cs2rig_clips:
            wm.cs2rig_clip = catalog_mod.clip_label(clip)
    target = importer.find_weapon_target(context)
    if target is not None:
        _choose(wm, "cs2rig_weapon", importer.weapon_key(target), _weapon_items)
        skin = paint.worn_skin_settings(target)
        if skin is not None:
            _choose(wm, "cs2rig_skin", skin[0], _skin_enum(None, context))
            wm.cs2rig_wear, wm.cs2rig_seed = skin[1], skin[2]
        _sync_sticker_controls(context)


@bpy.app.handlers.persistent
def _on_file_loaded(*_args) -> None:
    """After a file opens, relink maps and fill the lists from the cache when it is already there."""
    context = bpy.context
    try:
        relinked = maps.relink_linked_maps(session.cache_root() / "maps")
        if relinked:
            log_mod.logger.info("relinked %d map(s) to the maps folder", relinked)
        if not _agent_items:
            if session.setup_problem() is not None:
                return
            current = session.get()
            if not current.has_lists():
                return  # Read Game Files reads them the first time; that takes a while
            _fill_lists(context, current)
        _sync_to_scene(context)
    except Exception:  # never stop a file from opening
        log_mod.logger.exception("filling the lists after opening a file failed")


_NEEDS_REFRESH = "Read the game files first"
_KIND_LABELS = {
    "rif": "Rifle",
    "pist": "Pistol",
    "smg": "SMG",
    "snip": "Sniper rifle",
    "shot": "Shotgun",
    "mach": "Machine gun",
    "knife": "Knife",
    "other": "Weapon",
}


class CS2RIG_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    game_dir: StringProperty(
        name="Game Folder",
        subtype="DIR_PATH",
        description="The 'Counter-Strike Global Offensive' folder. Leave empty to find it through Steam",
        update=lambda self, context: session.reset(),
    )
    converter_path: StringProperty(
        name="Source2Viewer-CLI",
        subtype="FILE_PATH",
        description="Path to Source2Viewer-CLI, which reads the game files",
        update=lambda self, context: session.reset(),
    )
    cache_dir: StringProperty(
        name="Cache Folder",
        subtype="DIR_PATH",
        description="Where converted game files are kept. Leave empty for the default",
        update=lambda self, context: session.reset(),
    )
    log_dir: StringProperty(
        name="Log Folder",
        subtype="DIR_PATH",
        description=f"Where {log_mod.LOG_NAME} is written. Leave empty for the default, next to the cache",
        update=lambda self, context: _start_log(),
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_dir")
        layout.prop(self, "converter_path")
        layout.prop(self, "cache_dir")
        row = layout.row()
        row.operator("cs2rig.clear_cache", text="Clear Old Game Versions").everything = False
        row.operator("cs2rig.clear_cache", text="Clear All Converted Files").everything = True
        layout.prop(self, "log_dir")
        row = layout.row()
        row.operator("cs2rig.open_log_folder", icon="FILE_FOLDER")
        log_file = log_mod.current_file()
        row.label(text=log_mod.private(str(log_file)) if log_file else "The log folder cannot be written.")


class CS2RIG_ClipName(bpy.types.PropertyGroup):
    pass  # the clip path is stored in the built-in `name`


class CS2RIG_StickerName(bpy.types.PropertyGroup):
    pass  # the sticker search label is stored in the built-in `name`


class CS2RIG_OT_download_converter(bpy.types.Operator):
    bl_idname = "cs2rig.download_converter"
    bl_label = "Download Converter (65 MB)"
    bl_description = (
        f"Download Source2Viewer-CLI {CLI_VERSION} from its GitHub release. "
        "Needs online access in Preferences > System. Blender waits until it finishes"
    )

    @_reports_errors
    def execute(self, context):
        if not bpy.app.online_access:
            _report_error(self, context, "Blender is set to work offline. Allow online access in Preferences > System.")
            return {"CANCELLED"}
        session.download_converter()
        self.report({"INFO"}, "The converter is ready.")
        return {"FINISHED"}


class CS2RIG_OT_check_update(bpy.types.Operator):
    bl_idname = "cs2rig.check_update"
    bl_label = "Check for Updates"
    bl_description = "Ask GitHub whether a newer CS2 Rigs is out. Nothing is downloaded until you choose to update"

    def execute(self, context):
        if not updater.start_check(_addon_version(), asked=True):
            _report_error(self, context, "Blender is set to work offline. Allow online access in Preferences > System.")
            return {"CANCELLED"}
        if bpy.app.background:
            updater.wait()
        return {"FINISHED"}


class CS2RIG_OT_install_update(bpy.types.Operator):
    bl_idname = "cs2rig.install_update"
    bl_label = "Update Available!"
    bl_description = "Download the newer CS2 Rigs from GitHub and install it over this one. Restart Blender afterwards"

    @classmethod
    def poll(cls, context):
        return updater.status == "available" and updater.release is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self,
            event,
            title=f"Update to CS2 Rigs {updater.release.version}",
            message=f"Download CS2 Rigs {updater.release.version} from GitHub and install it over {_addon_version()}? Restart Blender afterwards.",
            confirm_text="Update",
        )

    @_reports_errors
    def execute(self, context):
        version = updater.release.version
        package_file = updater.download()
        updater.install_soon(package_file, __package__, version)
        self.report({"INFO"}, f"Installing CS2 Rigs {version}. Restart Blender when it is done.")
        return {"FINISHED"}


class CS2RIG_OT_show_preferences(bpy.types.Operator):
    bl_idname = "cs2rig.show_preferences"
    bl_label = "Set Game Folder..."
    bl_description = "Open the add-on preferences to choose the Counter-Strike Global Offensive folder"

    def execute(self, context):
        bpy.ops.preferences.addon_show(module=__package__)
        return {"FINISHED"}


class CS2RIG_OT_open_log_folder(bpy.types.Operator):
    bl_idname = "cs2rig.open_log_folder"
    bl_label = "Open Log Folder"
    bl_description = f"Show the folder with {log_mod.LOG_NAME}, to attach to a bug report"

    @_reports_errors
    def execute(self, context):
        log_file = log_mod.current_file()
        if log_file is None:
            _report_error(self, context, "The log folder cannot be written. Choose another Log Folder.")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=str(log_file.parent))
        return {"FINISHED"}


class CS2RIG_OT_clear_cache(bpy.types.Operator):
    bl_idname = "cs2rig.clear_cache"
    bl_label = "Clear Converted Files"
    bl_description = "Delete converted game files. They are converted again when next needed; built maps take minutes each"

    everything: BoolProperty()

    def invoke(self, context, event):
        if self.everything:
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Clear All Converted Files",
                message=(
                    "Delete every converted file, built maps included? Saved files that link a map "
                    "lose it until it is built again, which takes minutes per map."
                ),
                confirm_text="Delete",
                icon="WARNING",
            )
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Clear Old Game Versions",
            message="Delete files converted for older game versions? Built maps are kept, since saved files may link to them.",
            confirm_text="Delete",
        )

    @_reports_errors
    def execute(self, context):
        if _building() is not None:
            _report_error(self, context, "A map is being built. Wait for it to finish.")
            return {"CANCELLED"}
        if _preview_job is not None and _preview_job.running:
            _report_error(self, context, "Clip previews are being built. Wait for them to finish.")
            return {"CANCELLED"}
        if _glove_job is not None and _glove_job.busy:
            _report_error(self, context, "Gloves are being made. Wait for them to finish.")
            return {"CANCELLED"}
        if _finish_job is not None and _finish_job.busy:
            _report_error(self, context, "A finish is being made. Wait for it to finish.")
            return {"CANCELLED"}
        cache = session.get().cache
        if self.everything:
            targets, kept = cache.stale_dirs() + ([cache.version_dir] if cache.version_dir.is_dir() else []), []
            if cache.map_root.is_dir():
                targets += [cache.map_root]
        else:
            targets, kept = cache_mod.without_maps(cache.stale_dirs(), cache.map_root)
            maps.relink_linked_maps(cache.map_root)  # the open file links the moved maps
        before = sum(cache_mod.folder_size(target) for target in targets)
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        freed = before - sum(cache_mod.folder_size(target) for target in targets if target.exists())
        session.reset()
        note = f" Kept {len(kept)} built map(s) that saved files may link to." if kept else ""
        self.report({"INFO"}, f"Freed {freed / 1e9:.1f} GB.{note}")
        return {"FINISHED"}


class CS2RIG_OT_refresh(bpy.types.Operator):
    bl_idname = "cs2rig.refresh"
    bl_label = "Read Game Files"
    bl_description = (
        "Read the agent, weapon, finish, glove, clip and map lists from the installed game. "
        "About 30 seconds the first time, then instant"
    )

    @classmethod
    def poll(cls, context):
        problem = session.setup_problem()
        if problem is not None:
            cls.poll_message_set(problem[1])
            return False
        return True

    @_reports_errors
    def execute(self, context):
        if session.check_game_version():
            log_mod.logger.info("the game was updated; reading its files again")
        current = session.get()
        _fill_lists(context, current)
        _sync_to_scene(context)
        log_mod.logger.info("game files %s, %d finishes can be applied", current.cache.version_dir.name, len(_paintable))
        self.report({"INFO"}, f"Found {len(_agent_items)} agents, {len(_clip_labels)} clips and {len(_maps)} maps.")
        return {"FINISHED"}


class CS2RIG_OT_import_agent(bpy.types.Operator):
    bl_idname = "cs2rig.import_agent"
    bl_label = "Import Agent"
    bl_description = "Convert the chosen agent from the game files and add it to the scene"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_agent_items)

    @_reports_errors
    def execute(self, context):
        current = session.get()
        agent = current.agent(context.window_manager.cs2rig_agent)
        importer.import_agent(context, current, agent)
        _fill_clips(context)
        self.report({"INFO"}, f"Imported {agent.label}.")
        return {"FINISHED"}


class CS2RIG_OT_import_viewmodel(bpy.types.Operator):
    bl_idname = "cs2rig.import_viewmodel"
    bl_label = "Import First-Person"
    bl_description = "Import first-person viewmodel arms with the chosen agent's sleeves, gloves and a calibrated POV camera"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_agent_items)

    @_reports_errors
    def execute(self, context):
        current = session.get()
        wm = context.window_manager
        agent = current.agent(wm.cs2rig_agent)
        preset = wm.cs2rig_pov_preset if wm.cs2rig_pov_preset in ("classic", "desktop") else "classic"
        viewmodel.import_viewmodel(context, current, agent, preset=preset)
        _sync_to_scene(context)
        self.report({"INFO"}, f"Imported first-person arms for {agent.label}.")
        return {"FINISHED"}


class CS2RIG_OT_look_through_pov(bpy.types.Operator):
    bl_idname = "cs2rig.look_through_pov"
    bl_label = "Look Through POV Camera"
    bl_description = "Set the first-person POV camera as the scene camera and switch the viewport to look through it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return importer.find_viewmodel_arms(context) is not None

    @_reports_errors
    def execute(self, context):
        armature = importer.find_viewmodel_arms(context)
        cam = viewmodel.look_through_pov_camera(context, armature)
        self.report({"INFO"}, f"Active camera set to {cam.name}.")
        return {"FINISHED"}


class _AgentOperator(bpy.types.Operator):
    """Base for operators that act only on a third-person agent."""

    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return importer.find_agent_armature(context) is not None


class _CharacterOperator(bpy.types.Operator):
    """Base for operators that act on either a third-person agent or a first-person arms rig."""

    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return importer.find_character_rig(context) is not None


class CS2RIG_OT_fix_eyes(_AgentOperator):
    bl_idname = "cs2rig.fix_eyes"
    bl_label = "Rebuild Eye Materials"
    bl_description = (
        "Rebuild the eye materials on the selected agent. Only agents imported with 0.1.0 need this; "
        "new imports already have them"
    )

    @_reports_errors
    def execute(self, context):
        agents_fixed, materials_rebuilt = eyes.fix_selected_eyes(context, session.get())
        if materials_rebuilt:
            self.report({"INFO"}, f"Rebuilt {materials_rebuilt} eye material(s) on {agents_fixed} agent(s).")
        else:
            self.report({"INFO"}, "No eye materials found on the selected agent(s).")
        return {"FINISHED"}


class CS2RIG_OT_load_clip(_CharacterOperator):
    bl_idname = "cs2rig.load_clip"
    bl_label = "Load Clip"
    bl_description = "Convert the chosen game animation and play it on the selected agent or first-person rig"

    @classmethod
    def poll(cls, context):
        if not _clip_labels:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        return super().poll(context)

    @_reports_errors
    def execute(self, context):
        label = context.window_manager.cs2rig_clip
        if not label:
            _report_error(self, context, "Choose a clip first.")
            return {"CANCELLED"}
        armature = importer.find_character_rig(context)
        with importer.working_on(context, armature):
            action = importer.load_clip(context, session.get(), armature, catalog_mod.CLIP_PREFIX + label)
        weapon = _held_weapon(armature)
        is_fp = importer.is_viewmodel_arms(armature)
        clip_fam = vm_core.viewmodel_family_of_clip(label) if is_fp else catalog_mod.family_of_clip(label)
        fits = (
            weapon is None
            or not clip_fam
            or clip_fam in (catalog_mod.clip_family(weapon), catalog_mod.SHARED_CLIP_FAMILY, "arms")
        )
        if action.get(importer.COMPOSED_PROP):
            self.report({"WARNING"}, f"Loaded {label}. It is additive: the game plays it on top of another clip, so here it is shown over the rest pose.")
        elif fits:
            self.report({"INFO"}, f"Loaded {label}.")
        else:
            # The weapon bone sits where this clip holds its own kind of weapon.
            self.report(
                {"WARNING"},
                f"Loaded {label}, a {clip_fam} clip. "
                f"The {weapon.label} will not sit in the hands; use a {catalog_mod.clip_family(weapon)} clip.",
            )
        return {"FINISHED"}


class CS2RIG_OT_build_clip_previews(bpy.types.Operator):
    bl_idname = "cs2rig.build_clip_previews"
    bl_label = "Build Clip Thumbnails"
    bl_description = (
        "Check every agent clip once and save a stick-figure thumbnail for the clip browser. "
        "About ten minutes, in the background"
    )
    bl_options = {"REGISTER"}

    _timer = None

    @classmethod
    def poll(cls, context):
        return bool(_agent_items) and (_preview_job is None or not _preview_job.running)

    def _start(self, context) -> clip_previews.PreviewJob:
        global _preview_job
        current = session.get()
        armature = importer.find_agent_armature(context)
        agent = current.agent(armature[importer.AGENT_KEY_PROP] if armature else _agent_items[0][0])
        _preview_job = clip_previews.PreviewJob(current, agent)
        log_mod.logger.info("building clip previews on %s", agent.key)
        return _preview_job

    def _finish(self, context, job) -> set[str]:
        if job.cancelled:
            log_mod.logger.info("clip previews cancelled after %d clips", job.done)
            self.report({"INFO"}, "Stopped building clip previews.")
            return {"CANCELLED"}
        if job.error:
            log_mod.logger.error("building clip previews failed: %s", job.error)
            _report_error(self, context, job.error)
            return {"CANCELLED"}
        log_mod.logger.info("built clip previews: %d clips checked", job.done)
        self.report({"INFO"}, f"Checked {job.done} clips and saved their previews.")
        return {"FINISHED"}

    @_reports_errors
    def execute(self, context):
        # Used by scripts and background runs: wait for the job.
        job = self._start(context)
        while job.running:
            time.sleep(1.0)
        return self._finish(context, job)

    def invoke(self, context, event):
        try:
            self._start(context)
        except RuntimeError as exc:
            _report_error(self, context, str(exc))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(1.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, "Building clip previews in the background. Blender stays usable.")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if _preview_job is None:
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()
        if _preview_job.running:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context, _preview_job)


class CS2RIG_OT_cancel_clip_previews(bpy.types.Operator):
    bl_idname = "cs2rig.cancel_clip_previews"
    bl_label = "Cancel Clip Previews"
    bl_description = "Stop building clip previews now and close the background Blender"

    @classmethod
    def poll(cls, context):
        return _preview_job is not None and _preview_job.running

    @_reports_errors
    def execute(self, context):
        if _preview_job is not None:
            _preview_job.cancel()
        self.report({"INFO"}, "Stopping the clip previews.")
        return {"FINISHED"}


class CS2RIG_OT_cancel_map_build(bpy.types.Operator):
    bl_idname = "cs2rig.cancel_map_build"
    bl_label = "Cancel Map Build"
    bl_description = "Stop building the map now and remove its partial files"

    @classmethod
    def poll(cls, context):
        return _building() is not None

    @_reports_errors
    def execute(self, context):
        job = _building()
        if job is not None:
            job.cancel()
        self.report({"INFO"}, "Stopping the map build.")
        return {"FINISHED"}


class CS2RIG_OT_cancel_gloves(bpy.types.Operator):
    bl_idname = "cs2rig.cancel_gloves"
    bl_label = "Cancel Gloves"
    bl_description = "Stop making the glove finish now and remove the half-made images"

    @classmethod
    def poll(cls, context):
        return _glove_job is not None and not _glove_job.cancelled

    @_reports_errors
    def execute(self, context):
        if _glove_job is not None:
            _glove_job.cancel()
        self.report({"INFO"}, "Stopping the gloves.")
        return {"FINISHED"}


class CS2RIG_OT_cancel_finish(bpy.types.Operator):
    bl_idname = "cs2rig.cancel_finish"
    bl_label = "Cancel Finish"
    bl_description = "Stop making the finish now and remove the half-made images"

    @classmethod
    def poll(cls, context):
        return _finish_job is not None and not _finish_job.cancelled

    @_reports_errors
    def execute(self, context):
        if _finish_job is not None:
            _finish_job.cancel()
        self.report({"INFO"}, "Stopping the finish.")
        return {"FINISHED"}


class CS2RIG_OT_import_weapon(bpy.types.Operator):
    bl_idname = "cs2rig.import_weapon"
    bl_label = "Import Weapon"
    bl_description = "Convert the chosen weapon or knife from the game files and add it to the scene on its own"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _weapons:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        return True

    @_reports_errors
    def execute(self, context):
        weapon = _weapons.get(context.window_manager.cs2rig_weapon)
        if weapon is None:
            _report_error(self, context, "Choose a weapon first.")
            return {"CANCELLED"}
        importer.import_weapon(context, session.get(), weapon)
        self.report({"INFO"}, f"Imported {_weapon_title(weapon)}.")
        return {"FINISHED"}


class CS2RIG_OT_attach_weapon(_CharacterOperator):
    bl_idname = "cs2rig.attach_weapon"
    bl_label = "Hold Weapon"
    bl_description = "Convert the chosen weapon and put it in the selected agent's or first-person rig's hands, replacing the current one"

    @classmethod
    def poll(cls, context):
        if not _weapons:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        return super().poll(context)

    @_reports_errors
    def execute(self, context):
        weapon = _weapons.get(context.window_manager.cs2rig_weapon)
        if weapon is None:
            _report_error(self, context, "Choose a weapon first.")
            return {"CANCELLED"}
        armature = importer.find_character_rig(context)
        with importer.working_on(context, armature):
            importer.attach_weapon(context, session.get(), armature, weapon)
        _fill_clips(context)
        self.report({"INFO"}, f"Holding {weapon.label}.")
        return {"FINISHED"}


class CS2RIG_OT_remove_weapon(bpy.types.Operator):
    bl_idname = "cs2rig.remove_weapon"
    bl_label = "Remove Weapon"
    bl_description = "Remove the held weapon from the selected rig, or delete the selected standalone weapon"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        target = importer.find_weapon_target(context)
        return target is not None and bool(importer.weapon_key(target))

    @_reports_errors
    def execute(self, context):
        target = importer.find_weapon_target(context)
        was_rig = importer.is_character_rig(target)
        importer.remove_weapon(target)
        if was_rig:
            _fill_clips(context)
        return {"FINISHED"}


class CS2RIG_OT_apply_skin(bpy.types.Operator):
    bl_idname = "cs2rig.apply_skin"
    bl_label = "Apply Finish"
    bl_description = (
        "Put the chosen finish on the weapon, with the Weapon Wear and Weapon Seed above. "
        "About 10 seconds the first time"
    )
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    @classmethod
    def poll(cls, context):
        if not _kits:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        target = importer.find_weapon_target(context)
        # A job cancelled by opening a file may still be finishing on its thread.
        chosen = context.window_manager.cs2rig_skin
        return (
            target is not None
            and bool(importer.weapon_key(target))
            and chosen not in ("", "NONE")
            and (_finish_job is None or not _finish_job.busy)
        )

    def _start(self, context) -> paint.FinishJob | None:
        global _finish_job
        wm = context.window_manager
        kit = _kits.get(wm.cs2rig_skin)
        if kit is None:
            _report_error(self, context, "Choose a finish first.")
            return None
        _finish_job = paint.FinishJob(session.get(), importer.find_weapon_target(context), kit, wm.cs2rig_wear, wm.cs2rig_seed)
        return _finish_job

    def _finish(self, context, job: paint.FinishJob) -> set[str]:
        global _finish_job
        _finish_job = None
        if job.cancelled:
            return {"CANCELLED"}
        _clear_error(context)
        log_mod.logger.info(
            "finish %s wear %.3f seed %d%s",
            job.kit.name, job.wear, job.seed, f", without {', '.join(job.missing)}" if job.missing else "",
        )
        if job.missing:
            self.report({"WARNING"}, f"Applied {job.kit.label}, without its {' and '.join(job.missing)}.")
        else:
            self.report({"INFO"}, f"Applied {job.kit.label}.")
        _sync_sticker_controls(context)
        return {"FINISHED"}

    def _step(self, context, job: paint.FinishJob) -> bool:
        target = job.target()
        if target is None:
            return job.step(context)  # reports that the weapon is gone
        with importer.working_on(context, target):
            return job.step(context)

    @_reports_errors
    def execute(self, context):
        # Used by scripts and background runs: wait for the job.
        job = self._start(context)
        if job is None:
            return {"CANCELLED"}
        try:
            while not self._step(context, job):
                job.wait()
        except BaseException:
            global _finish_job
            _finish_job = None
            raise
        return self._finish(context, job)

    def invoke(self, context, event):
        try:
            started = self._start(context)
        except RuntimeError as exc:
            log_mod.logger.error("apply_skin failed: %s", exc)
            _report_error(self, context, str(exc))
            return {"CANCELLED"}
        if started is None:
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        _redraw_panels(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _finish_job
        job = _finish_job
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if job is None or job.cancelled:
            context.window_manager.event_timer_remove(self._timer)
            # Keep the job while its thread lives, so Apply Finish stays disabled.
            if job is None or not job.busy:
                _finish_job = None
            return {"CANCELLED"}
        try:
            done = self._step(context, job)
        except Exception as exc:
            context.window_manager.event_timer_remove(self._timer)
            _finish_job = None
            log_mod.logger.exception("applying a finish failed")
            _report_error(self, context, str(exc))
            _redraw_panels(context)
            return {"CANCELLED"}
        _redraw_panels(context)
        if not done:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context, job)


class CS2RIG_OT_remove_skin(bpy.types.Operator):
    bl_idname = "cs2rig.remove_skin"
    bl_label = "Remove Finish"
    bl_description = "Give the weapon its stock look back"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        target = importer.find_weapon_target(context)
        return target is not None and bool(paint.worn_skin(target))

    @_reports_errors
    def execute(self, context):
        target = importer.find_weapon_target(context)
        current = session.get() if stickers.applied_stickers(target) else None
        paint.remove_skin(target, session=current)
        _sync_sticker_controls(context)
        return {"FINISHED"}


class CS2RIG_OT_apply_sticker(bpy.types.Operator):
    bl_idname = "cs2rig.apply_sticker"
    bl_label = "Apply Sticker"
    bl_description = "Apply the chosen sticker onto the active weapon's selected slot"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _stickers_by_search:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        target = importer.find_weapon_target(context)
        return target is not None and bool(importer.weapon_key(target))

    @_reports_errors
    def execute(self, context):
        wm = context.window_manager
        chosen = wm.cs2rig_sticker.strip()
        kit = _stickers_by_search.get(chosen) or _stickers_by_material.get(chosen)
        if kit is None and chosen:
            hits = stickers_mod.search_stickers(list(_stickers_by_search.values()), chosen, limit=1)
            kit = hits[0] if hits else None
        if kit is None:
            _report_error(self, context, "Choose a sticker first.")
            return {"CANCELLED"}
        target = importer.find_weapon_target(context)
        slot = int(wm.cs2rig_sticker_slot)
        stickers.apply_sticker(session.get(), target, kit, slot, wear=wm.cs2rig_sticker_wear)
        _sync_sticker_controls(context)
        self.report({"INFO"}, f"Applied {kit.label} to Slot {slot + 1}.")
        return {"FINISHED"}


class CS2RIG_OT_remove_sticker(bpy.types.Operator):
    bl_idname = "cs2rig.remove_sticker"
    bl_label = "Remove Sticker"
    bl_description = "Remove the sticker from the selected slot"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        target = importer.find_weapon_target(context)
        if target is None or not importer.weapon_key(target):
            return False
        slot = int(context.window_manager.cs2rig_sticker_slot)
        return stickers.slot_sticker(target, slot) is not None

    @_reports_errors
    def execute(self, context):
        target = importer.find_weapon_target(context)
        slot = int(context.window_manager.cs2rig_sticker_slot)
        stickers.remove_sticker(session.get(), target, slot)
        _sync_sticker_controls(context)
        self.report({"INFO"}, f"Removed sticker from Slot {slot + 1}.")
        return {"FINISHED"}


class CS2RIG_OT_clear_stickers(bpy.types.Operator):
    bl_idname = "cs2rig.clear_stickers"
    bl_label = "Clear All"
    bl_description = "Remove all stickers from the active weapon"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        target = importer.find_weapon_target(context)
        return target is not None and bool(stickers.applied_stickers(target))

    @_reports_errors
    def execute(self, context):
        target = importer.find_weapon_target(context)
        stickers.clear_stickers(target)
        _sync_sticker_controls(context)
        self.report({"INFO"}, "Cleared all stickers.")
        return {"FINISHED"}


class CS2RIG_OT_reset_sticker_transform(bpy.types.Operator):
    bl_idname = "cs2rig.reset_sticker_transform"
    bl_label = "Reset Placement"
    bl_description = "Reset Offset, Scale and Rotation for the selected slot to the weapon's default"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        target = importer.find_weapon_target(context)
        if target is None or not importer.weapon_key(target):
            return False
        slot = int(context.window_manager.cs2rig_sticker_slot)
        return stickers.slot_sticker(target, slot) is not None

    @_reports_errors
    def execute(self, context):
        target = importer.find_weapon_target(context)
        slot = int(context.window_manager.cs2rig_sticker_slot)
        stickers.reset_slot_transform(session.get(), target, slot)
        _sync_sticker_controls(context)
        self.report({"INFO"}, f"Reset Slot {slot + 1} placement.")
        return {"FINISHED"}


class CS2RIG_OT_apply_gloves(_CharacterOperator):
    bl_idname = "cs2rig.apply_gloves"
    bl_label = "Apply Gloves"
    bl_description = (
        "Put the chosen glove finish on the selected agent's or first-person rig's hands, with the Glove Wear and Glove Seed above. "
        "Made in the background the first time"
    )

    _timer = None

    @classmethod
    def poll(cls, context):
        if not _glove_items:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        chosen = context.window_manager.cs2rig_glove
        # A job cancelled by opening a file may still be finishing on its thread.
        return (
            super().poll(context)
            and chosen not in ("", "NONE")
            and (_glove_job is None or not _glove_job.busy)
        )

    def _start(self, context) -> gloves.GloveJob | None:
        global _glove_job
        wm = context.window_manager
        kit = _kits.get(wm.cs2rig_glove)
        if kit is None:
            _report_error(self, context, "Choose gloves first.")
            return None
        armature = importer.find_character_rig(context)
        _glove_job = gloves.GloveJob(session.get(), armature, kit, wm.cs2rig_glove_wear, wm.cs2rig_glove_seed)
        return _glove_job

    def _finish(self, context, job: gloves.GloveJob) -> set[str]:
        global _glove_job
        _glove_job = None
        if job.cancelled:
            return {"CANCELLED"}
        _clear_error(context)
        log_mod.logger.info("gloves %s wear %.2f seed %d", job.kit.name, job.wear, job.seed)
        self.report({"INFO"}, f"Applied {gloves_mod.label(job.kit)}.")
        return {"FINISHED"}

    def _step(self, context, job: gloves.GloveJob) -> bool:
        armature = job.armature()
        if armature is None:
            return job.step(context)  # reports that the agent is gone
        with importer.working_on(context, armature):
            return job.step(context)

    @_reports_errors
    def execute(self, context):
        # Used by scripts and background runs: wait for the job.
        job = self._start(context)
        if job is None:
            return {"CANCELLED"}
        try:
            while not self._step(context, job):
                job.wait()
        except BaseException:
            global _glove_job
            _glove_job = None
            raise
        return self._finish(context, job)

    def invoke(self, context, event):
        try:
            started = self._start(context)
        except RuntimeError as exc:
            log_mod.logger.error("apply_gloves failed: %s", exc)
            _report_error(self, context, str(exc))
            return {"CANCELLED"}
        if started is None:
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        _redraw_panels(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _glove_job
        job = _glove_job
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if job is None or job.cancelled:
            context.window_manager.event_timer_remove(self._timer)
            # Keep the job while its thread lives, so Apply Gloves stays disabled.
            if job is None or not job.busy:
                _glove_job = None
            return {"CANCELLED"}
        try:
            done = self._step(context, job)
        except Exception as exc:
            context.window_manager.event_timer_remove(self._timer)
            _glove_job = None
            log_mod.logger.exception("applying gloves failed")
            _report_error(self, context, str(exc))
            _redraw_panels(context)
            return {"CANCELLED"}
        _redraw_panels(context)
        if not done:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context, job)


def _redraw_panels(context) -> None:
    for area in context.screen.areas if context.screen else ():
        if area.type == "VIEW_3D":
            area.tag_redraw()


@bpy.app.handlers.persistent
def _forget_glove_job(*_args) -> None:
    """A file being opened ends a glove job: its agent belongs to the file being closed."""
    if _glove_job is not None:
        _glove_job.cancel()


@bpy.app.handlers.persistent
def _forget_finish_job(*_args) -> None:
    """A file being opened ends a finish job: its weapon belongs to the file being closed."""
    if _finish_job is not None:
        _finish_job.cancel()


class CS2RIG_OT_remove_gloves(_CharacterOperator):
    bl_idname = "cs2rig.remove_gloves"
    bl_label = "Remove Gloves"
    bl_description = "Give the agent or first-person rig its default gloves back"

    @classmethod
    def poll(cls, context):
        armature = importer.find_character_rig(context)
        return armature is not None and bool(gloves.worn_gloves(armature))

    @_reports_errors
    def execute(self, context):
        gloves.remove_gloves(importer.find_character_rig(context))
        return {"FINISHED"}


class CS2RIG_OT_build_rig(_AgentOperator):
    bl_idname = "cs2rig.build_rig"
    bl_label = "Add Pose Controls"
    bl_description = (
        "Add hand and foot IK controls, elbow and knee poles, and foot roll to the selected agent. "
        "Game clips keep working"
    )

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and not rig.has_rig(armature)

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        importer.require_visible(armature)
        limbs = rig.build_rig(context, armature)
        self.report({"INFO"}, f"Added controls to {len(limbs)} limbs.")
        return {"FINISHED"}


class CS2RIG_OT_remove_rig(_AgentOperator):
    bl_idname = "cs2rig.remove_rig"
    bl_label = "Remove Pose Controls"
    bl_description = "Remove the pose controls from the selected agent. Limbs under IK go back to the game clip"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and rig.RIG_VERSION_PROP in armature.keys()

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        importer.require_visible(armature)
        rig.remove_rig(context, armature)
        return {"FINISHED"}


class CS2RIG_OT_pose_mode(bpy.types.Operator):
    bl_idname = "cs2rig.pose_mode"
    bl_label = "Switch to Pose Mode"
    bl_description = "Show the agent's pose controls, where they can be moved"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and rig.has_rig(armature) and context.mode != "POSE"

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        importer.require_visible(armature)
        importer._make_active(context, armature)
        bpy.ops.object.mode_set(mode="POSE")
        return {"FINISHED"}


def _limb_name(limb: str, side: str) -> str:
    return f"{'left' if side == 'L' else 'right'} {limb}"


class CS2RIG_OT_snap_limb(_AgentOperator):
    bl_idname = "cs2rig.snap_limb"
    bl_label = "Switch Limb"
    bl_description = "Switch this limb between the game pose (FK) and its IK control without moving it"

    limb: StringProperty()
    side: StringProperty()
    to_ik: BoolProperty()

    @classmethod
    def description(cls, context, properties):
        name = _limb_name(properties.limb, properties.side)
        if properties.to_ik:
            return f"Switch the {name} to its IK control without moving it"
        return f"Return the {name} to the game clip without moving it"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and rig.has_rig(armature)

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        if (self.limb, self.side) not in rig.rigged_limbs(armature):
            _report_error(self, context, "This limb has no IK controls.")
            return {"CANCELLED"}
        snap = rig.snap_ik_to_fk if self.to_ik else rig.snap_fk_to_ik
        snap(context, armature, self.limb, self.side)
        return {"FINISHED"}


class CS2RIG_OT_set_follow(_AgentOperator):
    bl_idname = "cs2rig.set_follow"
    bl_label = "Follow"
    bl_description = "Make the weapon follow the right hand, or the left hand follow the weapon, without moving anything"

    prop: StringProperty()
    on: BoolProperty()

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and rig.has_rig(armature)

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        if self.prop not in rig.follows(armature):
            _report_error(self, context, "This agent has no such control.")
            return {"CANCELLED"}
        rig.set_follow(context, armature, self.prop, self.on)
        return {"FINISHED"}


class CS2RIG_OT_add_map(bpy.types.Operator):
    bl_idname = "cs2rig.add_map"
    bl_label = "Add Map"
    bl_description = (
        "Bring the chosen map into the scene. The first time, the map is converted and built "
        "into its own file, which takes a few minutes and about 1 GB of disk"
    )
    bl_options = {"REGISTER", "UNDO"}

    # Set by a row's Rebuild button only; never remembered for the plain Add Map button.
    name: StringProperty(options={"SKIP_SAVE"})
    rebuild: BoolProperty(options={"SKIP_SAVE"})
    _timer = None

    @classmethod
    def poll(cls, context):
        return _building() is None and bool(_maps)

    def _chosen(self, context) -> catalog_mod.GameMap:
        game_map = _maps.get(self.name) or _maps.get(context.window_manager.cs2rig_map)
        if game_map is None:
            raise RuntimeError("Choose a map first.")
        if game_map.name in maps.maps_in_scene(context.scene) and not self.rebuild:
            raise RuntimeError(f"{game_map.name} is already in this scene.")
        return game_map

    def _add(self, context, library, game_map):
        if self.rebuild and game_map.name in maps.maps_in_scene(context.scene):
            if not maps.reload_map(game_map.name):
                self.report({"WARNING"}, f"Rebuilt {game_map.name}. The copy in this scene is unchanged.")
                return {"FINISHED"}
            self.report({"INFO"}, f"Rebuilt {game_map.name}.")
            return {"FINISHED"}
        link = context.window_manager.cs2rig_map_link
        use_map = context.window_manager.cs2rig_map_sky
        collection = maps.add_map(context, library, game_map.name, link)
        if collection is None:
            self.report({"INFO"}, f"Linked {game_map.name} to its new file.")
            return {"FINISHED"}
        maps.add_sky(context.scene, game_map.name, library, collection, link, use_map)
        probes = maps.probe_worlds(collection)
        if probes:
            context.window_manager.cs2rig_map_probe = f"{game_map.name}|{int(probes[0]['index'])}"
        _sync_hdri_controls(context)
        world = context.scene.world
        real = world is not None and world.get(MAP_NAME_PROP) == game_map.name
        if not use_map:
            sky = " Map sky and lights left off."
        elif real:
            sky = " Real CS2 sky and map lights applied."
        elif context.scene.world is not None:
            sky = " Map lights applied; the scene already had a sky."
        else:
            sky = " A plain sky was added for light."
        self.report({"INFO"}, f"{'Linked' if link else 'Appended'} {game_map.name}.{sky}")
        return {"FINISHED"}

    @_reports_errors
    def execute(self, context):
        # Used by scripts and background runs: wait for the build.
        game_map = self._chosen(context)
        library = maps.build_library(session.get(), game_map, bpy.app.binary_path, force=self.rebuild)
        return self._add(context, library, game_map)

    def invoke(self, context, event):
        global _map_job
        try:
            game_map = self._chosen(context)
            current = session.get()
            library = maps.prepare_library(current, game_map)
            maps.relink_linked_maps(current.cache.map_root)  # other scenes may link the old place
            if library.is_file() and not self.rebuild:
                return self._add(context, library, game_map)
        except RuntimeError as exc:
            log_mod.logger.error("%s failed: %s", _op_name(self), exc)
            _report_error(self, context, str(exc))
            return {"CANCELLED"}
        _map_job = maps.BuildJob(current, game_map, force=self.rebuild)
        log_mod.logger.info("%s %s", "rebuilding" if self.rebuild else "building", game_map.name)
        self._timer = context.window_manager.event_timer_add(1.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, f"Building {game_map.name}. Blender stays usable; this takes a few minutes.")
        return {"RUNNING_MODAL"}
    def modal(self, context, event):
        global _map_job
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if _map_job is None:
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()  # keeps the elapsed time in the panel moving
        if _map_job.running:
            return {"PASS_THROUGH"}
        job, _map_job = _map_job, None
        context.window_manager.event_timer_remove(self._timer)
        if job.cancelled:
            log_mod.logger.info("stopped building %s", job.game_map.name)
            self.report({"INFO"}, f"Stopped building {job.game_map.name}.")
            return {"CANCELLED"}
        if job.error:
            log_mod.logger.error("building %s failed: %s", job.game_map.name, job.error)
            _report_error(self, context, job.error)
            return {"CANCELLED"}
        log_mod.logger.info("built %s", job.game_map.name)
        try:
            return self._add(context, job.library, job.game_map)
        except RuntimeError as exc:
            log_mod.logger.error("%s failed: %s", _op_name(self), exc)
            _report_error(self, context, str(exc))
            return {"CANCELLED"}


class CS2RIG_OT_remove_map(bpy.types.Operator):
    bl_idname = "cs2rig.remove_map"
    bl_label = "Remove Map"
    bl_description = "Take this map out of the scene. Its built file stays, so adding it again is quick"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    @_reports_errors
    def execute(self, context):
        if self.name in maps.maps_in_scene(context.scene):
            maps.remove_map(context.scene, self.name)
        else:
            maps.remove_missing_map(self.name)
        return {"FINISHED"}


class CS2RIG_OT_apply_map_sky(bpy.types.Operator):
    bl_idname = "cs2rig.apply_map_sky"
    bl_label = "Apply Map Sky and Lights"
    bl_description = "Put this map's real CS2 sky in the scene and show its lights again"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()

    @_reports_errors
    def execute(self, context):
        collection = maps.maps_in_scene(context.scene).get(self.name)
        if collection is None:
            _report_error(self, context, f"{self.name} is not in this scene.")
            return {"CANCELLED"}
        library = maps.library_path(session.get(), _maps[self.name]) if self.name in _maps else None
        if not maps.apply_map_sky(context.scene, self.name, library, collection):
            _report_error(self, context, f"{self.name} was built without a sky. Rebuild it to get one.")
            return {"CANCELLED"}
        _sync_hdri_controls(context)
        self.report({"INFO"}, f"Applied {self.name}'s sky and lights.")
        return {"FINISHED"}


class CS2RIG_OT_apply_map_probes(bpy.types.Operator):
    bl_idname = "cs2rig.apply_map_probes"
    bl_label = "Apply Indoor Cubemap"
    bl_description = (
        "Light the scene with one of this map's baked indoor cubemap HDR probes. "
        "Pick which one in the Cubemap Probe list"
    )
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty()
    index: IntProperty(default=-1)  # -1: the probe picked in the panel, else the map's best one

    @_reports_errors
    def execute(self, context):
        collection = maps.maps_in_scene(context.scene).get(self.name)
        if collection is None:
            _report_error(self, context, f"{self.name} is not in this scene.")
            return {"CANCELLED"}
        index = self.index if self.index >= 0 else _chosen_probe(context, self.name)
        if not maps.probe_worlds(collection):
            _report_error(self, context, f"{self.name} has no baked indoor cubemap probes.")
            return {"CANCELLED"}
        library = maps.library_path(session.get(), _maps[self.name]) if self.name in _maps else None
        applied = maps.apply_map_probes(context.scene, self.name, library, collection, index if index >= 0 else None)
        if applied is None:
            _report_error(self, context, f"{self.name}'s cubemap probe could not be loaded. Rebuild the map.")
            return {"CANCELLED"}
        context.window_manager.cs2rig_map_probe = f"{self.name}|{applied}"
        _sync_hdri_controls(context)
        self.report({"INFO"}, f"Applied {self.name}'s indoor cubemap {applied}.")
        return {"FINISHED"}


class CS2RIG_OT_apply_hdri(bpy.types.Operator):
    bl_idname = "cs2rig.apply_hdri"
    bl_label = "Apply HDRI"
    bl_description = (
        "Light the scene with the chosen CS2 sky, in one click. It takes about a second the first time, "
        "then it is instant; HDRI Strength and HDRI Rotation adjust it live"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _hdri_skies:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        return context.scene is not None

    @_reports_errors
    def execute(self, context):
        skybox = _hdri_skies.get(context.window_manager.cs2rig_hdri)
        if skybox is None:
            _report_error(self, context, "Choose a CS2 HDRI first.")
            return {"CANCELLED"}
        current = session.get()
        sky = hdri_core.extract(
            current.converter,
            Path(current.install.main_package),
            skybox,
            current.cache.hdri_dir / skybox.key,
        )
        hdri.apply(context.scene, sky)
        _sync_hdri_controls(context)
        self.report({"INFO"}, f"Lit with {sky.label}. Adjust it with HDRI Strength and HDRI Rotation.")
        return {"FINISHED"}


class CS2RIG_OT_remove_hdri(bpy.types.Operator):
    bl_idname = "cs2rig.remove_hdri"
    bl_label = "Remove HDRI"
    bl_description = "Take the CS2 sky off the scene and put back the world it replaced"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hdri.is_cs2_sky_world(context.scene.world)

    @_reports_errors
    def execute(self, context):
        if not hdri.remove(context.scene):
            _report_error(self, context, "The scene has no CS2 HDRI to remove.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Removed the CS2 HDRI.")
        return {"FINISHED"}


class CS2RIG_OT_add_studio(bpy.types.Operator):
    bl_idname = "cs2rig.add_studio"
    bl_label = "Add Studio"
    bl_description = "Add 3-point studio lights, a backdrop and a camera framed to the active agent or selection"
    bl_options = {"REGISTER", "UNDO"}

    @_reports_errors
    def execute(self, context):
        wm = context.window_manager
        preset = studio_core.get_preset(wm.cs2rig_studio_preset)
        studio.add_or_update_studio(context, preset.key, wm.cs2rig_studio_backdrop)
        self.report({"INFO"}, f"Studio ready ({preset.label}).")
        return {"FINISHED"}


class CS2RIG_OT_remove_studio(bpy.types.Operator):
    bl_idname = "cs2rig.remove_studio"
    bl_label = "Remove Studio"
    bl_description = "Remove the studio lights, backdrop, camera and turntable without touching anything else"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return studio.has_studio(context.scene)

    @_reports_errors
    def execute(self, context):
        studio.remove_studio(context)
        self.report({"INFO"}, "Removed the studio.")
        return {"FINISHED"}


class CS2RIG_OT_frame_studio(bpy.types.Operator):
    bl_idname = "cs2rig.frame_studio"
    bl_label = "Frame Subject"
    bl_description = "Re-frame the studio camera, lights and backdrop around the active agent or selected object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return studio.has_studio(context.scene)

    @_reports_errors
    def execute(self, context):
        studio.frame_subject(context)
        self.report({"INFO"}, "Framed the studio on the subject.")
        return {"FINISHED"}


class CS2RIG_OT_toggle_turntable(bpy.types.Operator):
    bl_idname = "cs2rig.toggle_turntable"
    bl_label = "Turntable"
    bl_description = "Orbit the studio camera and lights 360 degrees across the scene frame range without altering agent animation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return studio.has_studio(context.scene)

    @_reports_errors
    def execute(self, context):
        enable = not studio.has_turntable(context.scene)
        studio.set_turntable(context, enable)
        self.report({"INFO"}, "Added 360° turntable." if enable else "Removed turntable.")
        return {"FINISHED"}


def _draw_update(layout) -> None:
    """The top row: whether a newer CS2 Rigs is out. Updating is always the user's choice."""
    if updater.status == "available":
        row = layout.row()
        row.alert = True
        row.scale_y = 1.4
        row.operator("cs2rig.install_update", text="Update Available!", icon="IMPORT")
        layout.label(text=f"CS2 Rigs {updater.release.version} is out; you have {_addon_version()}.")
    elif updater.status == "current":
        layout.label(text="CS2-Rigs is Updated", icon="CHECKMARK")
    elif updater.status == "installed":
        layout.label(text="Restart Blender to finish updating.", icon="FILE_REFRESH")
    elif updater.status == "checking":
        layout.label(text="Checking for updates...", icon="TIME")
    else:
        layout.operator("cs2rig.check_update", icon="URL")


def _agent(context) -> bpy.types.Object | None:
    return importer.find_agent_armature(context)


def _weapon_target(context) -> bpy.types.Object | None:
    return importer.find_weapon_target(context)


def _next_hint(context, armature: bpy.types.Object | None, standalone: bpy.types.Object | None = None) -> str:
    """One line that says what to do next in the state the panel is in."""
    if not _agent_items:
        return "Next: read the game files, then import an agent or weapon."
    if standalone is not None:
        if not paint.worn_skin(standalone):
            return "Next: choose a finish and press Apply Finish."
        if maps.maps_in_scene(context.scene):
            return "Next: adjust finish or import an agent."
        return "Next: add a map or import another weapon."
    if armature is None:
        return "Next: import an agent or a weapon."
    if not importer.held_weapon_key(armature):
        return "Next: choose a weapon and press Hold Weapon."
    action = armature.animation_data.action if armature.animation_data else None
    if action is None or not action.get(importer.CLIP_KEY_PROP):
        return "Next: choose a clip and press Load Clip."
    if importer.is_viewmodel_arms(armature):
        return "Next: adjust POV Camera, or add a map."
    if not rig.has_rig(armature):
        return "Next: add pose controls, or add a map."
    if maps.maps_in_scene(context.scene):
        return "Next: browse clips or adjust pose."
    return "Next: add a map or browse clips."


def _wear_text(wear: float) -> str:
    return f"{wear:.2f}".rstrip("0").rstrip(".") or "0"


def _applied_line(target: bpy.types.Object) -> str:
    settings = paint.worn_skin_settings(target)
    if settings is None:
        return ""
    kit = _kits.get(settings[0])
    label = kit.label if kit is not None else settings[0]
    return f"Applied: {label} · wear {_wear_text(settings[1])} · seed {settings[2]}"


def _wearing_line(armature: bpy.types.Object) -> str:
    settings = gloves.worn_glove_settings(armature)
    if settings is None:
        return ""
    kit = _kits.get(settings[0])
    label = gloves_mod.label(kit) if kit is not None else settings[0]
    return f"Wearing: {label} · wear {_wear_text(settings[1])} · seed {settings[2]}"


def _weapon_header(target: bpy.types.Object | None) -> str:
    weapon = _active_weapon(target)
    if weapon is None:
        return "None"
    settings = paint.worn_skin_settings(target)
    kit = _kits.get(settings[0]) if settings is not None else None
    return f"{_weapon_title(weapon)} · {kit.label}" if kit is not None else _weapon_title(weapon)


def _stickers_header(target: bpy.types.Object | None) -> str:
    applied = stickers.applied_stickers(target) if target is not None else []
    if not applied:
        return "None"
    return "1 sticker" if len(applied) == 1 else f"{len(applied)} stickers"


def _gloves_header(armature: bpy.types.Object) -> str:
    settings = gloves.worn_glove_settings(armature)
    if settings is None:
        return "None"
    kit = _kits.get(settings[0])
    return gloves_mod.label(kit) if kit is not None else settings[0]


def _clip_header(armature: bpy.types.Object) -> str:
    action = armature.animation_data.action if armature.animation_data else None
    if action is None:
        return "None"
    clip = action.get(importer.CLIP_KEY_PROP)
    label = catalog_mod.clip_label(clip) if clip else action.name
    return label.rsplit("/", 1)[-1]


def _map_header(context: bpy.types.Context) -> str:
    names = list(maps.maps_in_scene(context.scene))
    missing = len(maps.missing_maps())
    if missing:
        names.append(f"{missing} missing")
    world = context.scene.world
    if world is not None and world.get(WORLD_KIND_PROP) == "probe" and world.get(MAP_NAME_PROP) in names:
        label = str(world.get(PROBE_LABEL_PROP) or f"Cubemap {int(world.get(PROBE_INDEX_PROP, 0))}")
        return f"{world.get(MAP_NAME_PROP)} · {label}"
    return ", ".join(names) if names else "None"


def _chosen_probe(context: bpy.types.Context, name: str) -> int:
    """The probe picked in the panel when it belongs to this map, else -1 for the map's best."""
    map_name, _, index = context.window_manager.cs2rig_map_probe.partition("|")
    return int(index) if map_name == name and index.isdigit() else -1


def _probe_items(self, context) -> list[tuple[str, str, str]]:
    """Every indoor cubemap probe of every map in the scene, by map."""
    if context is None or getattr(context, "scene", None) is None:
        return _PRESS_REFRESH
    items = []
    for name, collection in maps.maps_in_scene(context.scene).items():
        for probe in maps.probe_worlds(collection):
            index = int(probe.get("index", -1))
            if index < 0:
                continue
            label = str(probe.get("label") or f"Cubemap {index}")
            items.append((f"{name}|{index}", f"{name} · {label}", f"{name}: {label}"))
    _probe_enum_items[:] = items or _PRESS_REFRESH
    return _probe_enum_items


def _studio_header(context: bpy.types.Context) -> str:
    if not studio.has_studio(context.scene):
        return f"HDRI {hdri.label(context.scene.world)}" if hdri.has_hdri(context.scene) else "Off"
    label = studio_core.get_preset(studio.studio_preset(context.scene)).label
    return f"{label} · Turntable" if studio.has_turntable(context.scene) else label


def _newer_build(game_map: catalog_mod.GameMap) -> bool:
    try:
        return maps.newer_build_possible(session.get(), game_map)
    except (RuntimeError, OSError):
        return False


def _thumbnail_count() -> tuple[int, int] | None:
    """How many clip thumbnails are saved, and how many agent clips there are."""
    if not _clip_labels:
        return None
    try:
        current = session.get()
    except RuntimeError:
        return None
    status = clipdata_mod.read_status(current.cache.clip_status_file())
    if status:
        total = sum(1 for entry in status["clips"].values() if entry.get("verdict") != "other-skeleton")
    else:
        total = sum(1 for label in _clip_labels if label.startswith(("world/", "ui_anims/")))
    made = 0
    folder = current.cache.clip_preview_dir()
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                if path.stat().st_size:
                    made += 1
            except OSError:
                continue
    return made, total


def _section(layout, idname: str, title: str, summary: str = "", *, default_closed: bool = False):
    """Collapsible section: title stays left-aligned; dimmed summary appears on the right only when collapsed."""
    header, body = layout.panel(idname, default_closed=default_closed)
    header.use_property_split = False
    if body is None and summary:
        row = header.row()
        left = row.row()
        left.alignment = "LEFT"
        left.label(text=title)
        right = row.row()
        right.alignment = "RIGHT"
        right.enabled = False
        right.label(text=summary)
    else:
        header.label(text=title)
    return body


def _pov_header(armature: bpy.types.Object) -> str:
    preset, fov, *_ = viewmodel.read_pov_settings(armature)
    label = vm_core.get_preset(preset).label
    return f"{label} · {fov:g}°"


def _draw_agent(layout, context, armature: bpy.types.Object | None) -> None:
    if armature is not None and importer.is_viewmodel_arms(armature):
        summary = f"FP · {_agent_title(armature)}"
    elif armature is not None:
        summary = _agent_title(armature)
    else:
        summary = "None"
    body = _section(layout, "cs2rig_agent", "Agent", summary)
    if body is None:
        return
    body.use_property_split = True
    body.prop(context.window_manager, "cs2rig_agent")
    body.operator("cs2rig.import_agent", icon="ARMATURE_DATA")
    body.operator("cs2rig.import_viewmodel", icon="VIEW_CAMERA")
    if not _agent_items:
        body.label(text="Read the game files first.", icon="INFO")
    elif armature is None:
        body.label(text="Select an imported agent.", icon="INFO")


def _draw_weapon(
    layout,
    context,
    target: bpy.types.Object | None,
) -> None:
    body = _section(layout, "cs2rig_weapon", "Weapon", _weapon_header(target))
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_weapon")
    acting_on_rig = target is not None and importer.is_character_rig(target)
    row = body.row(align=True)
    if acting_on_rig:
        row.operator("cs2rig.attach_weapon", icon="ADD")
        row.operator("cs2rig.remove_weapon", text="", icon="X")
        body.operator("cs2rig.import_weapon", text="Import Standalone", icon="IMPORT")
    else:
        row.operator("cs2rig.import_weapon", icon="IMPORT")
        row.operator("cs2rig.remove_weapon", text="", icon="X")
    if target is None or not importer.weapon_key(target):
        if acting_on_rig:
            body.label(text="No weapon held.", icon="INFO")
        elif any(importer.is_standalone_weapon(obj) for obj in context.scene.objects):
            body.label(text="Select an imported weapon.", icon="INFO")
        else:
            body.label(text="No weapon imported.", icon="INFO")
        return
    body.prop(wm, "cs2rig_skin")
    body.prop(wm, "cs2rig_wear", slider=True)
    body.prop(wm, "cs2rig_seed")
    row = body.row(align=True)
    if _finish_job is not None and not _finish_job.cancelled:
        row.label(text=f"{_finish_job.stage}...", icon="TIME")
        row.operator("cs2rig.cancel_finish", text="Cancel", icon="X")
    else:
        row.operator("cs2rig.apply_skin", icon="BRUSH_DATA")
        row.operator("cs2rig.remove_skin", text="", icon="X")
    applied = _applied_line(target)
    if applied:
        body.label(text=applied, icon="CHECKMARK")


def _draw_stickers(layout, context, target: bpy.types.Object) -> None:
    body = _section(layout, "cs2rig_stickers", "Stickers", _stickers_header(target), default_closed=True)
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_sticker_slot")
    body.prop_search(wm, "cs2rig_sticker", wm, "cs2rig_stickers", text="Sticker", icon="IMAGE_DATA")
    row = body.row(align=True)
    row.operator("cs2rig.apply_sticker", icon="ADD")
    row.operator("cs2rig.remove_sticker", text="", icon="X")
    slot = int(wm.cs2rig_sticker_slot)
    active = stickers.slot_sticker(target, slot)
    if active is not None:
        body.label(text=f"Slot {slot + 1}: {active.label or active.material}", icon="CHECKMARK")
        body.prop(wm, "cs2rig_sticker_wear", slider=True)
        body.prop(wm, "cs2rig_sticker_offset_x")
        body.prop(wm, "cs2rig_sticker_offset_y")
        body.prop(wm, "cs2rig_sticker_scale")
        body.prop(wm, "cs2rig_sticker_rotation")
        body.operator("cs2rig.reset_sticker_transform", icon="LOOP_BACK")
    if stickers.applied_stickers(target):
        body.operator("cs2rig.clear_stickers", icon="TRASH")


def _draw_gloves(layout, context, armature: bpy.types.Object) -> None:
    body = _section(layout, "cs2rig_gloves", "Gloves", _gloves_header(armature), default_closed=True)
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_glove")
    body.prop(wm, "cs2rig_glove_wear", slider=True)
    body.prop(wm, "cs2rig_glove_seed")
    row = body.row(align=True)
    if _glove_job is not None and not _glove_job.cancelled:
        row.label(text=f"{_glove_job.stage}...", icon="TIME")
        row.operator("cs2rig.cancel_gloves", text="Cancel", icon="X")
    else:
        row.operator("cs2rig.apply_gloves", icon="MOD_CLOTH")
        row.operator("cs2rig.remove_gloves", text="", icon="X")
    wearing = _wearing_line(armature)
    if wearing:
        body.label(text=wearing, icon="CHECKMARK")


def _draw_animation(layout, context, armature: bpy.types.Object) -> None:
    body = _section(layout, "cs2rig_animation", "Animation", _clip_header(armature))
    if body is None:
        return
    wm = context.window_manager
    weapon = _held_weapon(armature)
    body.prop(wm, "cs2rig_match_clips", text=f"Only clips for {_weapon_title(weapon)}")
    body.prop_search(wm, "cs2rig_clip", wm, "cs2rig_clips", text="Clip", icon="ACTION")
    row = body.row(align=True)
    row.operator("cs2rig.load_clip", icon="PLAY")
    row.operator("cs2rig.browse_clips", icon="VIEWZOOM")


def _draw_pov(layout, context, armature: bpy.types.Object) -> None:
    body = _section(layout, "cs2rig_pov", "POV Camera", _pov_header(armature))
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_pov_preset")
    body.prop(wm, "cs2rig_pov_fov", slider=True)
    body.prop(wm, "cs2rig_pov_offset_x", slider=True)
    body.prop(wm, "cs2rig_pov_offset_y", slider=True)
    body.prop(wm, "cs2rig_pov_offset_z", slider=True)
    body.operator("cs2rig.look_through_pov", icon="CAMERA_DATA")


def _draw_pose(layout, context, armature: bpy.types.Object) -> None:
    added = rig.has_rig(armature)
    body = _section(layout, "cs2rig_pose", "Pose Controls", "On" if added else "Off", default_closed=True)
    if body is None:
        return
    if not added:
        body.operator("cs2rig.build_rig", icon="CON_KINEMATIC")
        return
    if context.mode != "POSE":
        body.operator("cs2rig.pose_mode", icon="ARMATURE_DATA")
        body.label(text="Pose Mode shows the controls.", icon="INFO")
    for limb, side in rig.rigged_limbs(armature):
        switch = rig.switch_prop(limb, side)
        active = armature.get(switch, 0.0) >= 0.5
        row = body.row(align=True)
        row.prop(armature, f'["{switch}"]', text=f"{'Left' if side == 'L' else 'Right'} {limb.title()}", slider=True)
        op = row.operator("cs2rig.snap_limb", text="IK", depress=active)
        op.limb, op.side, op.to_ik = limb, side, True
        op = row.operator("cs2rig.snap_limb", text="FK", depress=not active)
        op.limb, op.side, op.to_ik = limb, side, False
    for prop in rig.follows(armature):
        on = armature[prop] >= 0.5
        op = body.operator(
            "cs2rig.set_follow",
            text=rig.FOLLOWS[prop][2],
            depress=on,
            icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT",
        )
        op.prop, op.on = prop, not on
    body.operator("cs2rig.remove_rig", icon="X")


def _draw_map(layout, context) -> None:
    body = _section(layout, "cs2rig_map", "Map", _map_header(context), default_closed=True)
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_map")
    body.prop(wm, "cs2rig_map_link")
    body.prop(wm, "cs2rig_map_sky")
    collections = maps.maps_in_scene(context.scene)
    if any(maps.probe_worlds(collection) for collection in collections.values()):
        body.prop(wm, "cs2rig_map_probe")
    job = _building()
    if job is not None:
        row = body.row(align=True)
        row.label(text=f"Building {job.game_map.name}  {job.elapsed}", icon="TIME")
        row.operator("cs2rig.cancel_map_build", text="Cancel", icon="X")
    else:
        body.operator("cs2rig.add_map", icon="WORLD")
    for name, collection in collections.items():
        row = body.row(align=True)
        kind = "linked" if collection.library is not None else "copied"
        row.label(text=f"{name} · {kind}", icon="OUTLINER_COLLECTION")
        row.operator("cs2rig.apply_map_sky", text="", icon="LIGHT").name = name
        if maps.probe_worlds(collection):
            row.operator("cs2rig.apply_map_probes", text="", icon="IMAGE_DATA").name = name
        row.operator("cs2rig.remove_map", text="", icon="X").name = name
        game_map = _maps.get(name)
        if game_map is not None and _newer_build(game_map):
            op = body.operator("cs2rig.add_map", text=f"Rebuild {name}", icon="FILE_REFRESH")
            op.name, op.rebuild = name, True
    for name in maps.missing_maps():
        row = body.row(align=True)
        row.alert = True
        row.label(text=f"{name}: file missing", icon="ERROR")
        row.operator("cs2rig.remove_map", text="", icon="X").name = name


def _draw_studio(layout, context) -> None:
    body = _section(layout, "cs2rig_studio", "Studio", _studio_header(context), default_closed=True)
    if body is None:
        return
    body.use_property_split = True
    wm = context.window_manager
    body.prop(wm, "cs2rig_hdri")
    row = body.row(align=True)
    row.operator("cs2rig.apply_hdri", icon="WORLD")
    row.operator("cs2rig.remove_hdri", text="", icon="X")
    sliders = body.column()
    sliders.use_property_split = True
    sliders.enabled = hdri.has_hdri(context.scene)
    sliders.prop(wm, "cs2rig_hdri_strength")
    sliders.prop(wm, "cs2rig_hdri_rotation")
    body.prop(wm, "cs2rig_studio_preset")
    body.prop(wm, "cs2rig_studio_backdrop")
    if not studio.has_studio(context.scene):
        body.operator("cs2rig.add_studio", icon="LIGHT_AREA")
        return
    turntable = studio.has_turntable(context.scene)
    row = body.row(align=True)
    row.operator("cs2rig.frame_studio", icon="CAMERA_DATA")
    row.operator(
        "cs2rig.toggle_turntable",
        text="Turntable",
        depress=turntable,
        icon="CHECKBOX_HLT" if turntable else "CHECKBOX_DEHLT",
    )
    body.operator("cs2rig.remove_studio", icon="X")


def _draw_tools(layout, context) -> None:
    body = _section(layout, "cs2rig_tools", "Tools", default_closed=True)
    if body is None:
        return
    count = _thumbnail_count()
    if count is not None:
        made, total = count
        body.label(text=f"Clip thumbnails: {made:,} of {total:,}", icon="IMAGE_DATA")
    if _preview_job is not None and _preview_job.running:
        row = body.row(align=True)
        row.label(text=_preview_job.progress, icon="TIME")
        row.operator("cs2rig.cancel_clip_previews", text="Cancel", icon="X")
    else:
        body.operator("cs2rig.build_clip_previews", icon="IMAGE_DATA")
    body.operator("cs2rig.fix_eyes", icon="MATERIAL")
    body.operator("cs2rig.open_log_folder", icon="FILE_FOLDER")


class CS2RIG_PT_main(bpy.types.Panel):
    bl_idname = "CS2RIG_PT_main"
    bl_label = "CS2 Rigs"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2 Rigs"

    def draw_header(self, context):
        if _agent_items:
            self.layout.operator("cs2rig.refresh", text="", icon="FILE_REFRESH")

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        _draw_update(layout)
        problem = session.setup_problem()
        if problem is not None and problem[0] == "game":
            col = layout.column()
            col.alert = True
            col.label(text=problem[1], icon="ERROR")
            layout.operator("cs2rig.show_preferences", icon="PREFERENCES")
        elif problem is not None:
            layout.label(text="The converter is needed once (65 MB).", icon="INFO")
            layout.operator("cs2rig.download_converter", icon="IMPORT")
        if not _agent_items and problem is None:
            col = layout.column()
            col.scale_y = 1.4
            col.operator("cs2rig.refresh", icon="FILE_REFRESH")
            layout.label(text="About 30 seconds the first time.", icon="TIME")
        armature = importer.find_character_rig(context)
        is_fp = armature is not None and importer.is_viewmodel_arms(armature)
        weapon_target = _weapon_target(context)
        standalone = weapon_target if weapon_target is not None and importer.is_standalone_weapon(weapon_target) else None
        if standalone is not None:
            layout.label(text=f"Acting on {_standalone_title(standalone)}", icon="OBJECT_DATA")
        elif is_fp:
            layout.label(text=f"Acting on First-Person · {_agent_title(armature)}", icon="VIEW_CAMERA")
        elif armature is not None:
            layout.label(text=f"Acting on {_agent_title(armature)}", icon="ARMATURE_DATA")
        layout.label(text=_next_hint(context, armature, standalone), icon="INFO")
        if wm.cs2rig_error:
            col = layout.column()
            col.alert = True
            col.label(text=wm.cs2rig_error, icon="ERROR")
            layout.operator("cs2rig.open_log_folder", icon="FILE_FOLDER")
        if not _agent_items:
            return
        _draw_agent(layout, context, armature)
        _draw_weapon(layout, context, weapon_target)
        if weapon_target is not None and importer.weapon_key(weapon_target):
            _draw_stickers(layout, context, weapon_target)
        if armature is not None:
            _draw_gloves(layout, context, armature)
            _draw_animation(layout, context, armature)
            if is_fp:
                _draw_pov(layout, context, armature)
            else:
                _draw_pose(layout, context, armature)
        _draw_map(layout, context)
        _draw_studio(layout, context)
        _draw_tools(layout, context)


_classes = (
    CS2RIG_Preferences,
    CS2RIG_ClipName,
    CS2RIG_StickerName,
    CS2RIG_OT_download_converter,
    CS2RIG_OT_check_update,
    CS2RIG_OT_install_update,
    CS2RIG_OT_show_preferences,
    CS2RIG_OT_open_log_folder,
    CS2RIG_OT_clear_cache,
    CS2RIG_OT_refresh,
    CS2RIG_OT_import_agent,
    CS2RIG_OT_import_viewmodel,
    CS2RIG_OT_look_through_pov,
    CS2RIG_OT_fix_eyes,
    CS2RIG_OT_load_clip,
    CS2RIG_OT_build_clip_previews,
    CS2RIG_OT_cancel_clip_previews,
    CS2RIG_OT_cancel_map_build,
    CS2RIG_OT_cancel_gloves,
    CS2RIG_OT_cancel_finish,
    CS2RIG_OT_import_weapon,
    CS2RIG_OT_attach_weapon,
    CS2RIG_OT_remove_weapon,
    CS2RIG_OT_apply_skin,
    CS2RIG_OT_remove_skin,
    CS2RIG_OT_apply_sticker,
    CS2RIG_OT_remove_sticker,
    CS2RIG_OT_clear_stickers,
    CS2RIG_OT_reset_sticker_transform,
    CS2RIG_OT_apply_gloves,
    CS2RIG_OT_remove_gloves,
    CS2RIG_OT_build_rig,
    CS2RIG_OT_remove_rig,
    CS2RIG_OT_pose_mode,
    CS2RIG_OT_snap_limb,
    CS2RIG_OT_set_follow,
    CS2RIG_OT_add_map,
    CS2RIG_OT_remove_map,
    CS2RIG_OT_apply_map_sky,
    CS2RIG_OT_apply_map_probes,
    CS2RIG_OT_apply_hdri,
    CS2RIG_OT_remove_hdri,
    CS2RIG_OT_add_studio,
    CS2RIG_OT_remove_studio,
    CS2RIG_OT_frame_studio,
    CS2RIG_OT_toggle_turntable,
    CS2RIG_PT_main,
)


def register():
    global _exit_hooked
    for cls in _classes:
        bpy.utils.register_class(cls)
    if not _exit_hooked:
        atexit.register(_cancel_jobs)
        _exit_hooked = True
    clip_browser.register()
    if _forget_glove_job not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_forget_glove_job)
    if _forget_finish_job not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_forget_finish_job)
    if _on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_loaded)
    _start_log()
    updater.start_check(_addon_version())
    wm = bpy.types.WindowManager
    wm.cs2rig_agent = EnumProperty(name="Agent", description="The agent to import. CT agents are listed first", items=lambda self, context: _agent_items or _PRESS_REFRESH)
    wm.cs2rig_weapon = EnumProperty(name="Weapon", description="The weapon or knife to import, or put in the selected agent's hands", items=lambda self, context: _weapon_items or _PRESS_REFRESH)
    wm.cs2rig_skin = EnumProperty(name="Finish", description="The finish for the selected or held weapon. Only finishes made for this weapon are listed", items=_skin_enum)
    wm.cs2rig_wear = FloatProperty(
        name="Weapon Wear",
        description="How worn the weapon finish is, from 0 (factory new) to 1 (battle-scarred). Kept inside the range the finish allows",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    wm.cs2rig_seed = IntProperty(
        name="Weapon Seed",
        description="Pattern seed, 0 to 1000. Places the pattern, wear and grime where the game puts them",
        min=0,
        max=1000,
        default=0,
    )
    wm.cs2rig_stickers = CollectionProperty(type=CS2RIG_StickerName)
    wm.cs2rig_sticker_slot = EnumProperty(
        name="Slot",
        description="Weapon sticker slot (1 to 5)",
        items=_sticker_slot_items,
        default="0",
        update=_on_sticker_slot_changed,
    )
    wm.cs2rig_sticker = StringProperty(
        name="Sticker",
        description="Search and choose a weapon sticker to apply to the selected slot",
    )
    wm.cs2rig_sticker_wear = FloatProperty(
        name="Scrape",
        description="How scraped the sticker is, from 0 (fresh) to 1 (heavily scraped). Updates live",
        min=0.0,
        max=1.0,
        default=0.0,
        update=_on_sticker_param_changed,
    )
    wm.cs2rig_sticker_offset_x = FloatProperty(
        name="Offset X",
        description="Horizontal placement offset in sticker UV space. Updates live",
        min=-0.5,
        max=0.5,
        default=0.0,
        step=0.5,
        precision=3,
        update=_on_sticker_param_changed,
    )
    wm.cs2rig_sticker_offset_y = FloatProperty(
        name="Offset Y",
        description="Vertical placement offset in sticker UV space. Updates live",
        min=-0.5,
        max=0.5,
        default=-0.4,
        step=0.5,
        precision=3,
        update=_on_sticker_param_changed,
    )
    wm.cs2rig_sticker_scale = FloatProperty(
        name="Scale",
        description="Inverse sticker size in sticker UV space (smaller value = larger sticker). Updates live",
        min=1.0,
        max=40.0,
        default=14.1,
        step=10.0,
        precision=2,
        update=_on_sticker_param_changed,
    )
    wm.cs2rig_sticker_rotation = FloatProperty(
        name="Rotation",
        description="Sticker rotation in degrees. Updates live",
        min=-180.0,
        max=180.0,
        default=0.0,
        step=100.0,
        precision=1,
        update=_on_sticker_param_changed,
    )
    wm.cs2rig_glove = EnumProperty(name="Gloves", description="The glove finish to put on the selected agent's hands", items=_glove_enum)
    wm.cs2rig_glove_wear = FloatProperty(
        name="Glove Wear",
        description="How worn the gloves are, from 0 (factory new) to 1 (battle-scarred). Kept inside the range the finish allows",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    wm.cs2rig_glove_seed = IntProperty(
        name="Glove Seed",
        description="Varies where the pattern and wear fall, 0 to 1000. The placement is not the one the game gives that seed",
        min=0,
        max=1000,
        default=0,
    )
    wm.cs2rig_map = EnumProperty(name="Map", description="The map to add to the scene. Backdrop scenes are the small scenes behind the game's menus", items=lambda self, context: _map_items or _PRESS_REFRESH)
    wm.cs2rig_map_link = BoolProperty(
        name="Link to Map File",
        description="Keep the map in its own file and link to it, so this file stays small. Turn off to copy the map into this file",
        default=True,
    )
    wm.cs2rig_map_sky = BoolProperty(
        name="Map Sky & Lights",
        description="Use the map's real CS2 sky and its calibrated lights. Turn off for the plain fallback sky and no map lights",
        default=True,
    )
    wm.cs2rig_map_probe = EnumProperty(
        name="Cubemap Probe",
        description="Which baked indoor cubemap of the maps in this scene to use for its lighting",
        items=_probe_items,
    )
    wm.cs2rig_clips = CollectionProperty(type=CS2RIG_ClipName)
    wm.cs2rig_clip = StringProperty(name="Clip", description="The game animation to load on the selected agent")
    wm.cs2rig_match_clips = BoolProperty(
        name="Only clips for the held weapon",
        description="List only clips made for the kind of weapon the agent holds, so it sits in the hands. Untick to see every clip",
        default=True,
        update=lambda self, context: _fill_clips(context),
    )
    wm.cs2rig_studio_preset = EnumProperty(
        name="Lighting",
        description="Studio lighting and backdrop mood",
        items=_studio_preset_items,
        default="studio",
        update=_on_studio_setting_changed,
    )
    wm.cs2rig_studio_backdrop = BoolProperty(
        name="Backdrop",
        description="Show the seamless curved studio backdrop behind the subject",
        default=True,
        update=_on_studio_setting_changed,
    )
    wm.cs2rig_hdri = EnumProperty(
        name="CS2 HDRI",
        description="A real in-game CS2 sky to light this scene with, with no map needed",
        items=_hdri_enum,
    )
    wm.cs2rig_hdri_strength = FloatProperty(
        name="HDRI Strength",
        description="Brightness of the active CS2 sky. Updates live, on a standalone HDRI or a map's own sky",
        min=0.0,
        max=50.0,
        soft_max=8.0,
        default=1.0,
        step=10,
        precision=2,
        update=_on_hdri_strength_changed,
    )
    wm.cs2rig_hdri_rotation = FloatProperty(
        name="HDRI Rotation",
        description="Turn the active CS2 sky about Z, in degrees from the direction its material gives it. Updates live",
        min=-360.0,
        max=360.0,
        default=0.0,
        step=100,
        precision=1,
        update=_on_hdri_rotation_changed,
    )
    wm.cs2rig_pov_preset = EnumProperty(
        name="Preset",
        description="CS2 viewmodel camera preset",
        items=_pov_preset_items,
        default="classic",
        update=_on_pov_preset_changed,
    )
    wm.cs2rig_pov_fov = FloatProperty(
        name="Viewmodel FOV",
        description="Source 2 4:3 Hor+ viewmodel_fov (54 to 68 degrees)",
        min=vm_core.FOV_MIN,
        max=vm_core.FOV_MAX,
        default=vm_core.DEFAULT_FOV,
        step=10,
        precision=1,
        update=_on_pov_slider_changed,
    )
    wm.cs2rig_pov_offset_x = FloatProperty(
        name="Offset X",
        description="viewmodel_offset_x in inches (positive moves right)",
        min=-5.0,
        max=5.0,
        soft_min=-2.0,
        soft_max=2.5,
        default=2.5,
        step=10,
        precision=2,
        update=_on_pov_slider_changed,
    )
    wm.cs2rig_pov_offset_y = FloatProperty(
        name="Offset Y",
        description="viewmodel_offset_y in inches (positive moves forward)",
        min=-5.0,
        max=5.0,
        soft_min=-2.0,
        soft_max=2.0,
        default=0.0,
        step=10,
        precision=2,
        update=_on_pov_slider_changed,
    )
    wm.cs2rig_pov_offset_z = FloatProperty(
        name="Offset Z",
        description="viewmodel_offset_z in inches (positive moves up)",
        min=-5.0,
        max=5.0,
        soft_min=-2.0,
        soft_max=2.0,
        default=-1.5,
        step=10,
        precision=2,
        update=_on_pov_slider_changed,
    )
    wm.cs2rig_error = StringProperty(name="Last Error")


def unregister():
    global _exit_hooked, _map_job, _preview_job, _glove_job, _finish_job
    clip_browser.unregister()
    if _forget_glove_job in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_forget_glove_job)
    if _forget_finish_job in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_forget_finish_job)
    if _on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_loaded)
    _cancel_jobs()
    _map_job = _preview_job = _glove_job = _finish_job = None
    if _exit_hooked:
        atexit.unregister(_cancel_jobs)
        _exit_hooked = False
    wm = bpy.types.WindowManager
    for name in (
        "cs2rig_error",
        "cs2rig_pov_offset_z",
        "cs2rig_pov_offset_y",
        "cs2rig_pov_offset_x",
        "cs2rig_pov_fov",
        "cs2rig_pov_preset",
        "cs2rig_hdri_rotation",
        "cs2rig_hdri_strength",
        "cs2rig_hdri",
        "cs2rig_studio_backdrop",
        "cs2rig_studio_preset",
        "cs2rig_glove_seed",
        "cs2rig_glove_wear",
        "cs2rig_glove",
        "cs2rig_sticker_rotation",
        "cs2rig_sticker_scale",
        "cs2rig_sticker_offset_y",
        "cs2rig_sticker_offset_x",
        "cs2rig_sticker_wear",
        "cs2rig_sticker",
        "cs2rig_sticker_slot",
        "cs2rig_stickers",
        "cs2rig_seed",
        "cs2rig_wear",
        "cs2rig_skin",
        "cs2rig_match_clips",
        "cs2rig_clip",
        "cs2rig_clips",
        "cs2rig_map_probe",
        "cs2rig_map_sky",
        "cs2rig_map_link",
        "cs2rig_map",
        "cs2rig_weapon",
        "cs2rig_agent",
    ):
        delattr(wm, name)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    session.reset()
    log_mod.close()
