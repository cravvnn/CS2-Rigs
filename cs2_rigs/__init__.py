"""CS2 Rigs: Counter-Strike 2 agents, clips and maps for Blender, built from your install."""

from __future__ import annotations

import functools
import platform
import shutil
import time
import tomllib
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from . import clip_browser, clip_previews, eyes, gloves, importer, maps, paint, rig, session, updater
from .core import cache as cache_mod
from .core import log as log_mod
from .core import catalog as catalog_mod
from .core import gloves as gloves_mod
from .core import skins as skins_mod
from .core.download import CLI_VERSION

# Filled by Refresh. Blender's enum callbacks need the item lists kept alive here.
_agent_items: list[tuple[str, str, str]] = []
_weapon_items: list[tuple[str, str, str]] = []
_map_items: list[tuple[str, str, str]] = []
_weapons: dict[str, catalog_mod.Weapon] = {}
_maps: dict[str, catalog_mod.GameMap] = {}
_clip_labels: list[str] = []
_kits: dict[str, skins_mod.PaintKit] = {}
_paintable: set[str] = set()  # kits whose recipe is in the game files
_skin_items: dict[str, list[tuple[str, str, str]]] = {}  # per weapon key
_glove_items: list[tuple[str, str, str]] = []
_map_job: maps.BuildJob | None = None
_preview_job: clip_previews.PreviewJob | None = None
_glove_job: gloves.GloveJob | None = None

_PRESS_REFRESH = [("NONE", "Press Refresh", "Read the lists from the game first")]


def _reports_errors(execute):
    """Show a failed step as an error in Blender instead of a traceback, and write each run to the log."""

    @functools.wraps(execute)
    def wrapper(self, context):
        started = time.monotonic()
        try:
            result = execute(self, context)
        except (RuntimeError, OSError) as exc:  # setup, converter, download, import and file problems
            log_mod.logger.error("%s failed: %s (%s)", _op_name(self), exc, _selection(context, _op_name(self)))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception:
            log_mod.logger.exception("%s stopped with an unexpected error (%s)", _op_name(self), _selection(context, _op_name(self)))
            raise
        log_mod.logger.info("%s %s in %.1f s", _op_name(self), ", ".join(sorted(result)).lower(), time.monotonic() - started)
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


def _skin_enum(self, context):
    """Finishes for the weapon the agent holds."""
    armature = importer.find_agent_armature(context)
    key = importer.held_weapon_key(armature) if armature else ""
    if key not in _skin_items:
        offered = skins_mod.kits_for(list(_kits.values()), skins_mod.item_name(key))
        _skin_items[key] = [
            (kit.name, kit.label, f"{kit.rarity or 'finish'} ({kit.name})") for kit in offered if kit.name in _paintable
        ]
    return _skin_items[key] or [("NONE", "No finishes", "This weapon has no finish that can be shown")]


def _glove_enum(self, context):
    return _glove_items or [("NONE", "No gloves", "Press Refresh to read the glove finishes from the game")]


def _fill_clips(context: bpy.types.Context) -> None:
    """List the clips to choose from: all of them, or only those made for the held weapon."""
    wm = context.window_manager
    clips = wm.cs2rig_clips
    clips.clear()
    if wm.cs2rig_match_clips:
        paths = [catalog_mod.CLIP_PREFIX + label for label in _clip_labels]
        weapon = _held_weapon(importer.find_agent_armature(context))
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
    _agent_items[:] = [
        (agent.key, f"{agent.side}  {agent.label}", agent.model_path) for agent in catalog_mod.catalog_agents(data)
    ]
    _weapons.clear()
    _weapons.update({weapon.key: weapon for weapon in catalog_mod.catalog_weapons(data)})
    _weapon_items[:] = [(weapon.key, weapon.label, weapon.model_path) for weapon in _weapons.values()]
    _maps.clear()
    _maps.update({game_map.name: game_map for game_map in catalog_mod.maps_in(current.install.maps_dir)})
    _map_items[:] = [
        (m.name, f"{m.name}  (backdrop)" if m.is_backdrop else m.name, m.package) for m in _maps.values()
    ]
    _clip_labels[:] = [catalog_mod.clip_label(clip) for clip in sorted(data.get("clips", []), key=catalog_mod.clip_order)]
    _kits.clear()
    _kits.update({kit.name: kit for kit in current.skins()})
    _paintable.clear()
    _paintable.update(kit.name for kit in _kits.values() if current.can_apply(kit))
    _skin_items.clear()
    _glove_items[:] = [
        (kit.name, gloves_mod.label(kit), f"{kit.rarity or 'finish'} ({kit.name})")
        for kit in gloves_mod.glove_kits(_kits.values())
        if kit.name in _paintable
    ]
    _fill_clips(context)


def _choose(wm, name: str, value, items) -> None:
    """Point a dropdown at `value` when it is one of its items."""
    if value and value in {item[0] for item in items}:
        setattr(wm, name, value)


def _sync_to_scene(context: bpy.types.Context) -> None:
    """Point the panel at what the scene's agent has on: its agent, weapon, finish, gloves and clip."""
    armature = importer.find_agent_armature(context)
    if armature is None:
        return
    wm = context.window_manager
    _choose(wm, "cs2rig_agent", armature.get(importer.AGENT_KEY_PROP), _agent_items)
    _choose(wm, "cs2rig_weapon", importer.held_weapon_key(armature), _weapon_items)
    _fill_clips(context)  # the clips depend on the held weapon
    skin = paint.worn_skin_settings(armature)
    if skin is not None:
        _choose(wm, "cs2rig_skin", skin[0], _skin_enum(None, context))
        wm.cs2rig_wear, wm.cs2rig_seed = skin[1], skin[2]
    glove = gloves.worn_glove_settings(armature)
    if glove is not None:
        _choose(wm, "cs2rig_glove", glove[0], _glove_items)
        wm.cs2rig_glove_wear, wm.cs2rig_glove_seed = glove[1], glove[2]
    action = armature.animation_data.action if armature.animation_data else None
    clip = action.get(importer.CLIP_KEY_PROP) if action is not None else None
    if clip and catalog_mod.clip_label(clip) in wm.cs2rig_clips:
        wm.cs2rig_clip = catalog_mod.clip_label(clip)


@bpy.app.handlers.persistent
def _on_file_loaded(*_args) -> None:
    """After a file opens, fill the lists from the cache (when it is already there) and match the scene."""
    context = bpy.context
    try:
        if not _agent_items:
            if session.setup_problem() is not None:
                return
            current = session.get()
            if not current.has_lists():
                return  # Refresh reads them the first time; that takes a while
            _fill_lists(context, current)
        _sync_to_scene(context)
    except Exception:  # never stop a file from opening
        log_mod.logger.exception("filling the lists after opening a file failed")


_NEEDS_REFRESH = "Press Refresh to read the game files first"


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


class CS2RIG_OT_download_converter(bpy.types.Operator):
    bl_idname = "cs2rig.download_converter"
    bl_label = "Download Converter"
    bl_description = (
        f"Download Source2Viewer-CLI {CLI_VERSION} (about 65 MB) from its GitHub releases. "
        "Blender waits until it finishes"
    )

    @_reports_errors
    def execute(self, context):
        if not bpy.app.online_access:
            self.report({"ERROR"}, "Blender is set to work offline. Allow online access in Preferences > System.")
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
            self.report({"ERROR"}, "Blender is set to work offline. Allow online access in Preferences > System.")
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
    bl_label = "Set Game Folder"
    bl_description = "Open the add-on preferences to choose the 'Counter-Strike Global Offensive' folder"

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
            self.report({"ERROR"}, "The log folder cannot be written. Choose another Log Folder.")
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
            self.report({"ERROR"}, "A map is being built. Wait for it to finish.")
            return {"CANCELLED"}
        if _preview_job is not None and _preview_job.running:
            self.report({"ERROR"}, "Clip previews are being built. Wait for them to finish.")
            return {"CANCELLED"}
        if _glove_job is not None and _glove_job.busy:
            self.report({"ERROR"}, "Gloves are being made. Wait for them to finish.")
            return {"CANCELLED"}
        cache = session.get().cache
        if self.everything:
            targets, kept = cache.stale_dirs() + ([cache.version_dir] if cache.version_dir.is_dir() else []), []
        else:
            targets, kept = cache_mod.without_maps(cache.stale_dirs())
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
    bl_label = "Refresh"
    bl_description = (
        "Read the lists of agents, weapons, finishes, gloves, clips and maps from the installed game. "
        "About half a minute the first time and after a game update, then instant"
    )

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
        self.report({"INFO"}, f"Imported {agent.label}.")
        return {"FINISHED"}


class _AgentOperator(bpy.types.Operator):
    """Base for operators that act on the agent the panel points at."""

    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return importer.find_agent_armature(context) is not None


class CS2RIG_OT_fix_eyes(_AgentOperator):
    bl_idname = "cs2rig.fix_eyes"
    bl_label = "Fix Eyes"
    bl_description = "Rebuild eye materials on the selected agent(s) so irises and pupils appear"

    @_reports_errors
    def execute(self, context):
        agents_fixed, materials_rebuilt = eyes.fix_selected_eyes(context, session.get())
        if materials_rebuilt:
            self.report({"INFO"}, f"Rebuilt {materials_rebuilt} eye material(s) on {agents_fixed} agent(s).")
        else:
            self.report({"INFO"}, "No eye materials found on the selected agent(s).")
        return {"FINISHED"}


class CS2RIG_OT_load_clip(_AgentOperator):
    bl_idname = "cs2rig.load_clip"
    bl_label = "Load Clip"
    bl_description = "Convert the chosen game animation and play it on the selected agent"

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
            self.report({"ERROR"}, "Choose a clip first.")
            return {"CANCELLED"}
        armature = importer.find_agent_armature(context)
        with importer.working_on(context, armature):
            action = importer.load_clip(context, session.get(), armature, catalog_mod.CLIP_PREFIX + label)
        weapon = _held_weapon(armature)
        if action.get(importer.COMPOSED_PROP):
            self.report({"WARNING"}, f"Loaded {label}. It is additive: the game plays it on top of another clip, so here it is shown over the rest pose.")
        elif catalog_mod.clip_fits(label, weapon) or not catalog_mod.family_of_clip(label):
            self.report({"INFO"}, f"Loaded {label}.")
        else:
            # The weapon bone sits where this clip holds its own kind of weapon.
            self.report(
                {"WARNING"},
                f"Loaded {label}, a {catalog_mod.family_of_clip(label)} clip. "
                f"The {weapon.label} will not sit in the hands; use a {catalog_mod.clip_family(weapon)} clip.",
            )
        return {"FINISHED"}


class CS2RIG_OT_build_clip_previews(bpy.types.Operator):
    bl_idname = "cs2rig.build_clip_previews"
    bl_label = "Build Clip Previews"
    bl_description = (
        "Load every clip made for agents once, in the background: check that it plays and save a small "
        "stick-figure preview for each. Takes about ten minutes the first time"
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

    def _finish(self, job) -> set[str]:
        if job.error:
            log_mod.logger.error("building clip previews failed: %s", job.error)
            self.report({"ERROR"}, job.error)
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
        return self._finish(job)

    def invoke(self, context, event):
        try:
            self._start(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(1.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, "Building clip previews in the background. Blender stays usable.")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER" or _preview_job is None:
            return {"PASS_THROUGH"}
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()
        if _preview_job.running:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(_preview_job)


class CS2RIG_OT_attach_weapon(_AgentOperator):
    bl_idname = "cs2rig.attach_weapon"
    bl_label = "Hold Weapon"
    bl_description = "Convert the chosen weapon and put it in the selected agent's hands, replacing the current one"

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
            self.report({"ERROR"}, "Choose a weapon first.")
            return {"CANCELLED"}
        armature = importer.find_agent_armature(context)
        with importer.working_on(context, armature):
            importer.attach_weapon(context, session.get(), armature, weapon)
        _fill_clips(context)
        self.report({"INFO"}, f"Holding {weapon.label}.")
        return {"FINISHED"}


class CS2RIG_OT_remove_weapon(_AgentOperator):
    bl_idname = "cs2rig.remove_weapon"
    bl_label = "Remove Weapon"
    bl_description = "Remove the weapon the selected agent is holding"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and bool(importer.held_weapon_key(armature))

    @_reports_errors
    def execute(self, context):
        importer.remove_weapon(importer.find_agent_armature(context))
        _fill_clips(context)
        return {"FINISHED"}


class CS2RIG_OT_apply_skin(_AgentOperator):
    bl_idname = "cs2rig.apply_skin"
    bl_label = "Apply Finish"
    bl_description = "Put the chosen finish on the held weapon, with the wear and seed above. About ten seconds the first time"

    @classmethod
    def poll(cls, context):
        if not _kits:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        armature = importer.find_agent_armature(context)
        return armature is not None and bool(importer.held_weapon_key(armature))

    @_reports_errors
    def execute(self, context):
        kit = _kits.get(context.window_manager.cs2rig_skin)
        if kit is None:
            self.report({"ERROR"}, "Choose a finish first.")
            return {"CANCELLED"}
        wm = context.window_manager
        missing = paint.apply_skin(context, session.get(), importer.find_agent_armature(context), kit, wm.cs2rig_wear, wm.cs2rig_seed)
        log_mod.logger.info(
            "finish %s wear %.2f seed %d%s", kit.name, wm.cs2rig_wear, wm.cs2rig_seed, f", without {', '.join(missing)}" if missing else ""
        )
        if missing:
            self.report({"WARNING"}, f"Applied {kit.label}, without its {' and '.join(missing)}.")
        else:
            self.report({"INFO"}, f"Applied {kit.label}.")
        return {"FINISHED"}


class CS2RIG_OT_remove_skin(_AgentOperator):
    bl_idname = "cs2rig.remove_skin"
    bl_label = "Remove Finish"
    bl_description = "Give the held weapon its stock look back"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and bool(paint.worn_skin(armature))

    @_reports_errors
    def execute(self, context):
        paint.remove_skin(importer.find_agent_armature(context))
        return {"FINISHED"}


class CS2RIG_OT_apply_gloves(_AgentOperator):
    bl_idname = "cs2rig.apply_gloves"
    bl_label = "Apply Gloves"
    bl_description = (
        "Put the chosen glove finish on the agent's hands, with the wear and seed above. "
        "The finish is made in the background the first time, so Blender stays usable"
    )

    _timer = None

    @classmethod
    def poll(cls, context):
        if not _glove_items:
            cls.poll_message_set(_NEEDS_REFRESH)
            return False
        # A job cancelled by opening a file may still be finishing on its thread.
        return super().poll(context) and (_glove_job is None or not _glove_job.busy)

    def _start(self, context) -> gloves.GloveJob | None:
        global _glove_job
        wm = context.window_manager
        kit = _kits.get(wm.cs2rig_glove)
        if kit is None:
            self.report({"ERROR"}, "Choose gloves first.")
            return None
        armature = importer.find_agent_armature(context)
        _glove_job = gloves.GloveJob(session.get(), armature, kit, wm.cs2rig_glove_wear, wm.cs2rig_glove_seed)
        return _glove_job

    def _finish(self, context, job: gloves.GloveJob) -> set[str]:
        global _glove_job
        _glove_job = None
        if job.cancelled:
            return {"CANCELLED"}
        log_mod.logger.info("gloves %s wear %.2f seed %d", job.kit.name, job.wear, job.seed)
        self.report({"INFO"}, f"Applied {gloves_mod.label(job.kit)}.")
        return {"FINISHED"}

    def _step(self, context, job: gloves.GloveJob) -> bool:
        armature = bpy.data.objects.get(job.armature_name)
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
            self.report({"ERROR"}, str(exc))
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
            _glove_job = None
            return {"CANCELLED"}
        try:
            done = self._step(context, job)
        except Exception as exc:
            context.window_manager.event_timer_remove(self._timer)
            _glove_job = None
            log_mod.logger.exception("applying gloves failed")
            self.report({"ERROR"}, str(exc))
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
        _glove_job.cancelled = True


class CS2RIG_OT_remove_gloves(_AgentOperator):
    bl_idname = "cs2rig.remove_gloves"
    bl_label = "Remove Gloves"
    bl_description = "Give the agent its default gloves back"

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and bool(gloves.worn_gloves(armature))

    @_reports_errors
    def execute(self, context):
        gloves.remove_gloves(importer.find_agent_armature(context))
        return {"FINISHED"}


class CS2RIG_OT_build_rig(_AgentOperator):
    bl_idname = "cs2rig.build_rig"
    bl_label = "Add Controls"
    bl_description = (
        "Add hand, foot, elbow and knee controls with foot roll to the selected agent, "
        "and make its twist bones follow. Game clips keep working"
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
    bl_label = "Remove Controls"
    bl_description = "Remove the controls from the selected agent. Limbs under IK go back to the game clip"

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


class CS2RIG_OT_snap_limb(_AgentOperator):
    bl_idname = "cs2rig.snap_limb"
    bl_label = "Switch Limb"
    bl_description = "Switch this limb between the game pose (FK) and its IK control without moving it"

    limb: StringProperty()
    side: StringProperty()
    to_ik: BoolProperty()

    @classmethod
    def poll(cls, context):
        armature = importer.find_agent_armature(context)
        return armature is not None and rig.has_rig(armature)

    @_reports_errors
    def execute(self, context):
        armature = importer.find_agent_armature(context)
        if (self.limb, self.side) not in rig.rigged_limbs(armature):
            self.report({"ERROR"}, "This limb has no IK controls.")
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
            self.report({"ERROR"}, "This agent has no such control.")
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

    _timer = None

    @classmethod
    def poll(cls, context):
        return _building() is None and bool(_maps)

    def _chosen(self, context) -> catalog_mod.GameMap:
        game_map = _maps.get(context.window_manager.cs2rig_map)
        if game_map is None:
            raise RuntimeError("Choose a map first.")
        if game_map.name in maps.maps_in_scene(context.scene):
            raise RuntimeError(f"{game_map.name} is already in this scene.")
        return game_map

    def _add(self, context, library, game_map):
        link = context.window_manager.cs2rig_map_link
        if maps.add_map(context, library, game_map.name, link) is None:
            self.report({"INFO"}, f"Linked {game_map.name} to its new file.")
            return {"FINISHED"}
        sky = " A plain sky was added for light." if maps.add_sky(context.scene) else ""
        self.report({"INFO"}, f"{'Linked' if link else 'Appended'} {game_map.name}.{sky}")
        return {"FINISHED"}

    @_reports_errors
    def execute(self, context):
        # Used by scripts and background runs: wait for the build.
        game_map = self._chosen(context)
        library = maps.build_library(session.get(), game_map, bpy.app.binary_path)
        return self._add(context, library, game_map)

    def invoke(self, context, event):
        global _map_job
        try:
            game_map = self._chosen(context)
            current = session.get()
            library = maps.library_path(current, game_map)
            if library.is_file():
                return self._add(context, library, game_map)
        except RuntimeError as exc:
            log_mod.logger.error("%s failed: %s", _op_name(self), exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _map_job = maps.BuildJob(current, game_map)
        log_mod.logger.info("building %s", game_map.name)
        self._timer = context.window_manager.event_timer_add(1.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, f"Building {game_map.name}. Blender stays usable; this takes a few minutes.")
        return {"RUNNING_MODAL"}
    def modal(self, context, event):
        global _map_job
        if event.type != "TIMER" or _map_job is None:
            return {"PASS_THROUGH"}
        for area in context.screen.areas if context.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()  # keeps the elapsed time in the panel moving
        if _map_job.running:
            return {"PASS_THROUGH"}
        job, _map_job = _map_job, None
        context.window_manager.event_timer_remove(self._timer)
        if job.error:
            log_mod.logger.error("building %s failed: %s", job.game_map.name, job.error)
            self.report({"ERROR"}, job.error)
            return {"CANCELLED"}
        log_mod.logger.info("built %s", job.game_map.name)
        try:
            return self._add(context, job.library, job.game_map)
        except RuntimeError as exc:
            log_mod.logger.error("%s failed: %s", _op_name(self), exc)
            self.report({"ERROR"}, str(exc))
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


class CS2RIG_PT_main(bpy.types.Panel):
    bl_idname = "CS2RIG_PT_main"
    bl_label = "CS2 Rigs"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CS2 Rigs"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        _draw_update(layout)
        problem = session.setup_problem() if not _agent_items else None
        if problem is not None and problem[0] == "game":
            col = layout.column()
            col.alert = True
            col.label(text="Counter-Strike 2 was not found.", icon="ERROR")
            layout.operator("cs2rig.show_preferences", icon="PREFERENCES")
        elif problem is not None:
            layout.label(text="The converter is needed once (about 65 MB).", icon="INFO")
            layout.operator("cs2rig.download_converter", icon="IMPORT")
        layout.operator("cs2rig.refresh", icon="FILE_REFRESH")
        armature = importer.find_agent_armature(context)

        box = layout.box()
        box.label(text="Agent")
        box.prop(wm, "cs2rig_agent", text="")
        box.operator("cs2rig.import_agent", icon="ARMATURE_DATA")
        box.operator("cs2rig.fix_eyes", icon="HIDE_OFF")
        if not _agent_items:
            box.label(text="Press Refresh to read the game files.", icon="INFO")
        elif armature is None:
            box.label(text="Select an imported agent.", icon="INFO")
        else:
            box.label(text=f"Acting on {armature.name.removesuffix('.skeleton')}", icon="ARMATURE_DATA")
            box.prop(wm, "cs2rig_glove", text="Gloves")
            row = box.row(align=True)
            row.prop(wm, "cs2rig_glove_wear", slider=True)
            row.prop(wm, "cs2rig_glove_seed")
            row = box.row(align=True)
            if _glove_job is not None and not _glove_job.cancelled:
                row.label(text=f"{_glove_job.stage}...", icon="TIME")
            else:
                row.operator("cs2rig.apply_gloves", icon="BRUSH_DATA")
            row.operator("cs2rig.remove_gloves", text="", icon="X")

        box = layout.box()
        box.label(text="Weapon")
        box.prop(wm, "cs2rig_weapon", text="")
        row = box.row(align=True)
        row.operator("cs2rig.attach_weapon", icon="ADD")
        row.operator("cs2rig.remove_weapon", text="", icon="X")
        if armature is not None and importer.held_weapon_key(armature):
            box.prop(wm, "cs2rig_skin", text="Finish")
            row = box.row(align=True)
            row.prop(wm, "cs2rig_wear", slider=True)
            row.prop(wm, "cs2rig_seed")
            row = box.row(align=True)
            row.operator("cs2rig.apply_skin", icon="BRUSH_DATA")
            row.operator("cs2rig.remove_skin", text="", icon="X")

        box = layout.box()
        box.label(text="Animation")
        box.prop_search(wm, "cs2rig_clip", wm, "cs2rig_clips", text="", icon="ACTION")
        box.prop(wm, "cs2rig_match_clips")
        row = box.row(align=True)
        row.operator("cs2rig.load_clip", icon="PLAY")
        row.operator("cs2rig.browse_clips", icon="VIEWZOOM")
        if _preview_job is not None and _preview_job.running:
            box.label(text=_preview_job.progress, icon="TIME")
        else:
            box.operator("cs2rig.build_clip_previews", icon="ARMATURE_DATA")

        box = layout.box()
        box.label(text="Controls")
        if armature is None or not rig.has_rig(armature):
            box.operator("cs2rig.build_rig", icon="CON_KINEMATIC")
        else:
            for limb, side in rig.rigged_limbs(armature):
                row = box.row(align=True)
                row.prop(armature, f'["{rig.switch_prop(limb, side)}"]', text=f"{limb.title()} {side} IK", slider=True)
                for label, to_ik in (("IK", True), ("FK", False)):
                    op = row.operator("cs2rig.snap_limb", text=label)
                    op.limb, op.side, op.to_ik = limb, side, to_ik
            for prop in rig.follows(armature):
                on = armature[prop] >= 0.5
                op = box.operator("cs2rig.set_follow", text=rig.FOLLOWS[prop][2], depress=on, icon="LINKED" if on else "UNLINKED")
                op.prop, op.on = prop, not on
            if context.mode != "POSE":
                box.label(text="Pose Mode shows the controls.", icon="INFO")
            box.operator("cs2rig.remove_rig", icon="X")

        box = layout.box()
        box.label(text="Map")
        box.prop(wm, "cs2rig_map", text="")
        box.prop(wm, "cs2rig_map_link")
        job = _building()
        if job is not None:
            box.label(text=f"Building {job.game_map.name}  {job.elapsed}", icon="TIME")
        else:
            box.operator("cs2rig.add_map", icon="WORLD")
        for name in maps.maps_in_scene(context.scene):
            row = box.row(align=True)
            row.label(text=name, icon="OUTLINER_COLLECTION")
            row.operator("cs2rig.remove_map", text="", icon="X").name = name
        for name in maps.missing_maps():
            row = box.row(align=True)
            row.alert = True
            row.label(text=f"{name}: file missing. Add Map relinks it", icon="ERROR")
            row.operator("cs2rig.remove_map", text="", icon="X").name = name


_classes = (
    CS2RIG_Preferences,
    CS2RIG_ClipName,
    CS2RIG_OT_download_converter,
    CS2RIG_OT_check_update,
    CS2RIG_OT_install_update,
    CS2RIG_OT_show_preferences,
    CS2RIG_OT_open_log_folder,
    CS2RIG_OT_clear_cache,
    CS2RIG_OT_refresh,
    CS2RIG_OT_import_agent,
    CS2RIG_OT_fix_eyes,
    CS2RIG_OT_load_clip,
    CS2RIG_OT_build_clip_previews,
    CS2RIG_OT_attach_weapon,
    CS2RIG_OT_remove_weapon,
    CS2RIG_OT_apply_skin,
    CS2RIG_OT_remove_skin,
    CS2RIG_OT_apply_gloves,
    CS2RIG_OT_remove_gloves,
    CS2RIG_OT_build_rig,
    CS2RIG_OT_remove_rig,
    CS2RIG_OT_snap_limb,
    CS2RIG_OT_set_follow,
    CS2RIG_OT_add_map,
    CS2RIG_OT_remove_map,
    CS2RIG_PT_main,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    clip_browser.register()
    if _forget_glove_job not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_forget_glove_job)
    if _on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_loaded)
    _start_log()
    updater.start_check(_addon_version())
    wm = bpy.types.WindowManager
    wm.cs2rig_agent = EnumProperty(name="Agent", description="The agent to import", items=lambda self, context: _agent_items or _PRESS_REFRESH)
    wm.cs2rig_weapon = EnumProperty(name="Weapon", description="The weapon or knife to put in the selected agent's hands", items=lambda self, context: _weapon_items or _PRESS_REFRESH)
    wm.cs2rig_skin = EnumProperty(name="Finish", description="The finish for the held weapon. Only finishes made for it are listed", items=_skin_enum)
    wm.cs2rig_wear = FloatProperty(
        name="Wear",
        description="How worn the finish is, from 0 (factory new) to 1 (battle-scarred). Kept inside the range the finish allows",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    wm.cs2rig_seed = IntProperty(
        name="Seed",
        description="Pattern seed (0 to 1000). Places the pattern, wear and grime where the game puts them",
        min=0,
        max=1000,
        default=0,
    )
    wm.cs2rig_glove = EnumProperty(name="Gloves", description="The glove finish to put on the selected agent's hands", items=_glove_enum)
    wm.cs2rig_glove_wear = FloatProperty(
        name="Wear",
        description="How worn the gloves are, from 0 (factory new) to 1 (battle-scarred). Kept inside the range the finish allows",
        min=0.0,
        max=1.0,
        default=0.0,
    )
    wm.cs2rig_glove_seed = IntProperty(
        name="Seed",
        description="Varies where the pattern and wear fall. The placement for a seed is not the one the game gives it",
        min=0,
        max=1000,
        default=0,
    )
    wm.cs2rig_map = EnumProperty(name="Map", description="The map to bring into the scene", items=lambda self, context: _map_items or _PRESS_REFRESH)
    wm.cs2rig_map_link = BoolProperty(
        name="Link",
        description="Keep the map in its own file and link to it, which keeps this file small. Turn off to copy the map into this file",
        default=True,
    )
    wm.cs2rig_clips = CollectionProperty(type=CS2RIG_ClipName)
    wm.cs2rig_clip = StringProperty(name="Clip", description="Game animation clip to load")
    wm.cs2rig_match_clips = BoolProperty(
        name="Only Clips for the Held Weapon",
        description="List only clips made for the kind of weapon the agent holds, so the weapon sits in its hands",
        default=True,
        update=lambda self, context: _fill_clips(context),
    )


def unregister():
    clip_browser.unregister()
    if _forget_glove_job in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_forget_glove_job)
    if _on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_loaded)
    _forget_glove_job()
    wm = bpy.types.WindowManager
    for name in ("cs2rig_glove_seed", "cs2rig_glove_wear", "cs2rig_glove", "cs2rig_seed", "cs2rig_wear", "cs2rig_skin", "cs2rig_match_clips", "cs2rig_clip", "cs2rig_clips", "cs2rig_map_link", "cs2rig_map", "cs2rig_weapon", "cs2rig_agent"):
        delattr(wm, name)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    if _preview_job is not None:
        _preview_job.cancel()  # stops its background Blender
    session.reset()
    log_mod.close()
