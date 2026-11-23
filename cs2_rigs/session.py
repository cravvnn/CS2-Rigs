"""Resolve the game install, converter and cache from the add-on preferences."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import bpy

from .core import catalog as catalog_mod
from .core import download
from .core import skins as skins_mod
from .core.cache import Cache, default_cache_root
from .core.converter import CLI_NAME, Converter, find_cli
from .core.steam import GameInstall, find_install

ENV_GAME = "CS2RIG_GAME_DIR"
ENV_CACHE = "CS2RIG_CACHE_DIR"


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

    def catalog(self) -> dict:
        if self._catalog is None:
            self._catalog = catalog_mod.load_or_build_catalog(
                self.converter, self.install.main_package, self.cache.catalog_file
            )
        return self._catalog

    def skins(self) -> list[skins_mod.PaintKit]:
        """Every weapon, knife and glove finish in the game. Nothing applies them to a model yet."""
        if self._skins is None:
            self._skins = skins_mod.load_or_build_catalogue(self.converter, self.install.main_package, self.cache.skins_file)
        return self._skins

    def paint_textures(self, kit: skins_mod.PaintKit) -> dict[str, Path]:
        """Convert a painted kit's textures; returns .png paths by role ('colour', 'roughness', 'normal')."""
        package = self.install.main_package
        textures = skins_mod.paint_textures(self.converter.dump(package, skins_mod.material_path(kit)))
        if "colour" not in textures:
            raise SetupError(f"{kit.label} has no painted texture in the game files.")
        return {
            role: self.converter.export_texture(package, path, self.cache.texture_dir(path))
            for role, path in textures.items()
        }

    def agent(self, key: str) -> catalog_mod.Agent:
        agent = next((a for a in catalog_mod.catalog_agents(self.catalog()) if a.key == key), None)
        if agent is None:
            raise SetupError(f"Agent '{key}' is not in the installed game.")
        return agent

    def export_model(self, model_path: str, out_dir: Path, clips: list[str], materials: bool) -> Path:
        """Convert a model from the main game package, or reuse the cached result."""
        return self.converter.export_model(self.install.main_package, model_path, out_dir, clips, materials)


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


def get() -> Session:
    global _session
    if _session is not None:
        return _session
    install = find_install(_setting("game_dir", ENV_GAME) or None)
    if install is None:
        raise SetupError("Counter-Strike 2 was not found. Set the game folder in the add-on preferences.")
    cache_root = Path(_setting("cache_dir", ENV_CACHE) or default_cache_root())
    tools_dir = Cache(cache_root, install.version()).tools_dir
    cli = find_cli(_setting("converter_path", "CS2RIG_S2V_CLI") or None) or download.installed_cli(tools_dir)
    if cli is None:
        raise ConverterMissing(
            f"{CLI_NAME} is needed to read the game files. Press Download Converter, "
            "or set its path in the add-on preferences."
        )
    converter = Converter(cli)
    _session = Session(install, converter, Cache(cache_root, install.version(), converter.tag()))
    return _session

def converter_ready() -> bool:
    """True when a converter is available, without raising. Cheap enough for panel drawing."""
    if _session is not None:
        return True
    try:
        get()
    except SetupError:
        return False
    return True


def download_converter() -> Path:
    """Fetch the pinned converter into the cache's tools folder."""
    install = find_install(_setting("game_dir", ENV_GAME) or None)
    version = install.version() if install else "unknown"
    cache = Cache(Path(_setting("cache_dir", ENV_CACHE) or default_cache_root()), version)
    path = download.download_cli(cache.tools_dir)
    reset()
    return path
