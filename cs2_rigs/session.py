"""Resolve the game install, converter and cache from the add-on preferences."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from .core import catalog as catalog_mod
from .core import clipdata
from .core import download
from .core import finish as finish_mod
from .core import glb as glb_mod
from .core import gloves as gloves_mod
from .core import skins as skins_mod
from .core import stickers as stickers_mod
from .core import viewmodel as viewmodel_mod
from .core.cache import Cache, default_cache_root
from .core.converter import CLI_NAME, Converter, exported_path, find_cli
from .core.steam import GameInstall, find_install

ENV_GAME = "CS2RIG_GAME_DIR"
ENV_CACHE = "CS2RIG_CACHE_DIR"
WEAPON_RIG_CLIP = "dropped"  # any clip will do: a weapon exported without one has no skeleton


class SetupError(RuntimeError):
    pass


class ConverterMissing(SetupError):
    pass


@dataclass
class Session:
    install: GameInstall
    converter: Converter
    cache: Cache
    _catalog: dict | None = None
    _skins: list | None = None
    _stickers: list | None = None
    _finishes: dict | None = None
    _materials: dict = field(default_factory=dict)
    _sticker_markup: dict = field(default_factory=dict)
    _clip_info: dict = field(default_factory=dict)
    _listing: list | None = None

    def entries(self) -> list:
        """The package's file listing, read once and shared by the lists that need it."""
        if self._listing is None:
            self._listing = self.converter.list_package(self.install.main_package)
        return self._listing

    def _done_with_listing(self) -> None:
        if self._catalog is not None and self._skins is not None and self._stickers is not None:
            self._listing = None  # the lists are built; the listing is large

    def catalog(self) -> dict:
        if self._catalog is None:
            self._catalog = catalog_mod.load_or_build_catalog(
                self.converter, self.install.main_package, self.cache.catalog_file, self.entries
            )
            self._done_with_listing()
        return self._catalog

    def skins(self) -> list[skins_mod.PaintKit]:
        """Every weapon, knife and glove finish in the game."""
        if self._skins is None:
            self._skins = skins_mod.load_or_build_catalogue(
                self.converter, self.install.main_package, self.cache.skins_file, self.entries
            )
            self._done_with_listing()
        return self._skins

    def stickers(self) -> list[stickers_mod.StickerKit]:
        """Every weapon sticker in the game, excluding graffiti and agent patches."""
        if self._stickers is None:
            self._stickers = stickers_mod.load_or_build_catalogue(
                self.converter, self.install.main_package, self.cache.stickers_file, self.entries
            )
            self._done_with_listing()
        return self._stickers

    def sticker_markup(self, model_path: str) -> dict[str, list[stickers_mod.StickerSlotDefault]]:
        """Parsed StickerMarkup slot metadata for one weapon model, cached on disk."""
        path = model_path.replace("\\", "/")
        if not path.endswith("_c"):
            path += "_c"
        if path not in self._sticker_markup:
            cached = stickers_mod.read_markup_cache(self.cache.sticker_markup_dir, path)
            if cached is None:
                try:
                    dump = self.converter.dump_block(self.install.main_package, path, "DATA")
                    cached = stickers_mod.parse_sticker_markup(dump)
                except RuntimeError:
                    cached = {}
                try:
                    stickers_mod.store_markup_cache(self.cache.sticker_markup_dir, path, cached)
                except OSError:
                    pass
            self._sticker_markup[path] = cached
        return self._sticker_markup[path]

    def finishes(self) -> dict:
        """Every finish's materials and recipes. Reading them takes a while the first time."""
        if self._finishes is None:
            self._finishes = finish_mod.load_or_build_library(self.converter, self.install.main_package, self.cache.finishes_file)
        return self._finishes

    def has_lists(self) -> bool:
        """True when the game files were read before, so the lists load without the converter."""
        stored = (
            (self.cache.catalog_file, catalog_mod.CATALOG_VERSION),
            (self.cache.skins_file, skins_mod.CATALOGUE_VERSION),
            (self.cache.stickers_file, stickers_mod.CATALOGUE_VERSION),
            (self.cache.finishes_file, finish_mod.LIBRARY_VERSION),
        )
        return all(_stored_version(path) == version for path, version in stored)

    def can_apply(self, kit: skins_mod.PaintKit) -> bool:
        if gloves_mod.is_glove_kit(kit):
            return gloves_mod.recipe_for(kit, self.finishes()["recipes"]) is not None
        return finish_mod.recipe_path(kit.name, kit.fields.get("composite_material_path", "")) in self.finishes()["recipes"]

    def material(self, path: str) -> dict:
        """The values of one material in the game package, by name."""
        path = path.replace("\\", "/").removesuffix("_c")
        if path not in self._materials:
            values = finish_mod.read_material(self.cache.materials_dir, path)
            if values is None:
                dump = finish_mod.split_dump(self.converter.dump(self.install.main_package, path + "_c"))
                if path not in dump:
                    raise SetupError(f"The material '{path}' is not in the game files.")
                values = finish_mod.material_values(dump[path])
                try:
                    finish_mod.store_material(self.cache.materials_dir, path, values)
                except OSError:
                    pass  # the cache only makes later sessions faster
            self._materials[path] = values
        return self._materials[path]

    def finish_values(self, kit: skins_mod.PaintKit, body_material: str, seed: int, wear: float) -> dict:
        """The shader values for a finish on the weapon body that uses `body_material`."""
        target = self.material(body_material)
        inputs = target.get("composite_inputs")
        if not inputs:
            raise SetupError("This weapon cannot take a finish: its material names no paint inputs.")
        recipe = finish_mod.recipe_path(kit.name, kit.fields.get("composite_material_path", ""))
        try:
            return finish_mod.resolve(self.finishes(), recipe, self.material(inputs), target, seed, wear)
        except KeyError as exc:
            raise SetupError(f"{kit.label} cannot be shown yet: {exc}") from exc

    def glove_values(self, kit: skins_mod.PaintKit, left: bool, seed: int, wear: float) -> dict:
        """The shader values for a glove finish on the left or the right glove."""
        recipe = gloves_mod.recipe_for(kit, self.finishes()["recipes"])
        if recipe is None:
            raise SetupError(f"{gloves_mod.label(kit)} cannot be shown yet: the game has no recipe for it.")
        try:
            return finish_mod.resolve(self.finishes(), recipe, {}, {}, seed, wear, gloves_mod.attributes(left))
        except KeyError as exc:
            raise SetupError(f"{gloves_mod.label(kit)} cannot be shown yet: {exc}") from exc

    def texture(self, path: str, raw: bool = False) -> Path:
        """Convert one texture named by a material (a '.vtex' path); returns the image file."""
        path = path.replace("\\", "/").removesuffix("_c") + "_c"
        suffix = ".exr" if "_pfm_" in path.rsplit("/", 1)[-1] else ".png"
        return self.converter.export_texture(self.install.main_package, path, self.cache.texture_dir(path, raw=raw), suffix, raw=raw)

    def agent(self, key: str) -> catalog_mod.Agent:
        agent = next((a for a in catalog_mod.catalog_agents(self.catalog()) if a.key == key), None)
        if agent is None:
            raise SetupError(f"Agent '{key}' is not in the installed game.")
        return agent

    def export_model(self, model_path: str, out_dir: Path, clips: list[str], materials: bool) -> Path:
        """Convert a model from the main game package, or reuse the cached result."""
        return self.converter.export_model(self.install.main_package, model_path, out_dir, clips, materials)

    def clip_info(self, clip: str) -> dict:
        """What a clip is: its skeleton, length, and whether it is additive.

        Read from the clip census when one has been built, otherwise from the clip itself.
        """
        if clip not in self._clip_info:
            census = clipdata.load_census(self.cache.clip_census_file())
            if census and clip in census["clips"]:
                self._clip_info[clip] = census["clips"][clip]
            else:
                lines = self.converter.stream(["-i", str(self.install.main_package), "-f", clip + ".vnmclip_c", "-b", "DATA"], timeout=300)
                found = dict(clipdata.parse_clip_dump(lines))
                if clip not in found:
                    raise SetupError(f"The clip '{catalog_mod.clip_label(clip)}' is not in the game files.")
                self._clip_info[clip] = found[clip]
        return self._clip_info[clip]

    def clip_census(self, progress=None) -> dict:
        """Facts about every clip in the game. Reading them takes about half a minute the first time."""
        census = clipdata.load_or_build_census(
            self.converter, self.install.main_package, self.catalog().get("clips", []), self.cache.clip_census_file(), progress
        )
        self._clip_info.update(census["clips"])
        return census

    def rig_export(self, agent: catalog_mod.Agent) -> Path:
        """The agent's skeleton only, which clips are put onto before Blender imports them.

        Made by stripping the model this agent was imported from, so a clip needs
        no converter run; a model that was never imported is exported once first.
        """
        out_dir = self.cache.clip_dir(agent.key, "rig")
        target = out_dir / "skeleton.glb"
        model = exported_path(self.cache.model_dir(agent.key), agent.model_path)
        if model.is_file():
            return glb_mod.strip_to_skeleton(model, target)
        rig = self.export_model(agent.model_path, out_dir, [catalog_mod.BASE_CLIP], materials=False)
        return glb_mod.strip_to_skeleton(rig, target)

    def weapon_rig_export(self, weapon: catalog_mod.Weapon) -> Path:
        """The weapon's skeleton only, which clips' weapon tracks are put onto."""
        out_dir = self.cache.clip_dir(f"weapon/{weapon.key}", "rig")
        target = out_dir / "skeleton.glb"
        model = exported_path(self.cache.model_dir(f"weapon/{weapon.key}"), weapon.model_path)
        if model.is_file():
            return glb_mod.strip_to_skeleton(model, target)
        rig = self.export_model(weapon.model_path, out_dir, [WEAPON_RIG_CLIP], materials=False)
        return glb_mod.strip_to_skeleton(rig, target)

    def fp_model_export(self) -> Path:
        """Convert the shared first-person arms model, or reuse the cached result."""
        return self.export_model(
            viewmodel_mod.WEAPON_ARMS_MODEL,
            self.cache.model_dir("viewmodel/arms"),
            [viewmodel_mod.WEAPON_ARMS_BASE_CLIP],
            materials=True,
        )

    def fp_rig_export(self) -> Path:
        """The first-person arms skeleton only, which viewmodel clips are put onto."""
        out_dir = self.cache.clip_dir("viewmodel/arms", "rig")
        target = out_dir / "skeleton.glb"
        model = exported_path(self.cache.model_dir("viewmodel/arms"), viewmodel_mod.WEAPON_ARMS_MODEL)
        if model.is_file():
            return glb_mod.strip_to_skeleton(model, target)
        rig = self.export_model(
            viewmodel_mod.WEAPON_ARMS_MODEL,
            out_dir,
            [viewmodel_mod.WEAPON_ARMS_BASE_CLIP],
            materials=False,
        )
        return glb_mod.strip_to_skeleton(rig, target)

    def export_clip(self, clip: str) -> tuple[Path, bool]:
        """Convert one clip on its own skeleton. Returns the file and whether it was composed
        over the bind pose (an additive clip the game has no full version of)."""
        source, compose = clipdata.source_clip(self.clip_info(clip))
        path = self.converter.export_clip(self.install.main_package, source or clip, self.cache.clip_export_dir(clip), compose)
        return path, compose


def _stored_version(path: Path) -> int | None:
    """The version a cached list was written with, read from the start of the file."""
    try:
        with open(path, encoding="utf-8") as handle:
            match = re.match(r'\{"version":\s*(\d+)', handle.read(64))
    except OSError:
        return None
    return int(match.group(1)) if match else None


_session: Session | None = None


def _preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _setting(name: str, env: str) -> str:
    prefs = _preferences()
    value = getattr(prefs, name, "") if prefs else ""
    return bpy.path.abspath(value) if value else os.environ.get(env, "")


def reset() -> None:
    global _session
    _session = None


def cache_root() -> Path:
    """Where converted files are kept, read without needing the game itself."""
    return Path(_setting("cache_dir", ENV_CACHE) or default_cache_root())


def get() -> Session:
    global _session
    if _session is not None:
        return _session
    game_dir = _setting("game_dir", ENV_GAME)
    install = find_install(game_dir or None)
    if install is None:
        if game_dir:
            raise SetupError(
                f"Counter-Strike 2 is not in {game_dir}. Set Game Folder to the 'Counter-Strike Global Offensive' folder."
            )
        raise SetupError("Counter-Strike 2 was not found. Set the game folder in the add-on preferences.")
    root = cache_root()
    tools_dir = Cache(root, install.version()).tools_dir
    cli = find_cli(_setting("converter_path", "CS2RIG_S2V_CLI") or None) or download.installed_cli(tools_dir)
    if cli is None:
        raise ConverterMissing(
            f"{CLI_NAME} is needed to read the game files. Press Download Converter, "
            "or set its path in the add-on preferences."
        )
    converter = Converter(cli)
    _session = Session(install, converter, Cache(root, install.version(), converter.tag()))
    return _session


def setup_problem() -> tuple[str, str] | None:
    """What stops the game files being read, as ("game" or "converter", message), or None.

    Never raises, and cheap enough for panel drawing.
    """
    if _session is not None:
        return None
    try:
        get()
    except ConverterMissing as exc:
        return "converter", str(exc)
    except SetupError as exc:
        return "game", str(exc)
    return None


def check_game_version() -> bool:
    """Start again from the game files if the game was updated since they were read. True if it was."""
    if _session is None:
        return False
    if _session.install.is_valid() and _session.install.version() == _session.cache.game_version:
        return False
    reset()
    return True


def download_converter() -> Path:
    """Fetch the pinned converter into the cache's tools folder."""
    cache = Cache(Path(_setting("cache_dir", ENV_CACHE) or default_cache_root()), "")
    path = download.download_cli(cache.tools_dir)
    reset()
    return path
