"""On-disk cache of converted assets, keyed to the game version and converter build. No Blender imports here."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from . import maplib

APP_DIR_NAME = "cs2-rigs"


def default_cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / APP_DIR_NAME


class Cache:
    def __init__(self, root: Path, game_version: str, converter_tag: str = ""):
        self.root = Path(root)
        self.game_version = game_version
        self.converter_tag = converter_tag

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def version_dir(self) -> Path:
        name = f"{self.game_version}-{self.converter_tag}" if self.converter_tag else self.game_version
        return self.cache_dir / name

    @property
    def catalog_file(self) -> Path:
        return self.version_dir / "catalog.json"

    @property
    def skins_file(self) -> Path:
        return self.version_dir / "skins.json"

    @property
    def stickers_file(self) -> Path:
        return self.version_dir / "stickers.json"

    @property
    def finishes_file(self) -> Path:
        return self.version_dir / "finishes.json"

    @property
    def materials_dir(self) -> Path:
        """Parsed material values, one small JSON per material."""
        return self.version_dir / "materials"

    @property
    def sticker_markup_dir(self) -> Path:
        """Parsed StickerMarkup slot metadata, one small JSON per weapon model."""
        return self.version_dir / "sticker_markup"

    def finish_dir(self, key: str) -> Path:
        """Where the textures composed for one finish, weapon body, wear and seed are kept."""
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "finishes" / digest

    def model_dir(self, model_key: str) -> Path:
        return self.version_dir / "models" / model_key.replace("/", "__")

    def clip_dir(self, model_key: str, clip: str) -> Path:
        digest = hashlib.sha1(clip.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "clips" / model_key.replace("/", "__") / digest

    def clip_export_dir(self, clip: str) -> Path:
        """Where one clip, converted on its own skeleton, is kept for every agent."""
        digest = hashlib.sha1(clip.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "clip_exports" / digest

    def clip_census_file(self) -> Path:
        return self.version_dir / "clip_census.json"

    def clip_status_file(self) -> Path:
        return self.version_dir / "clip_status.json"

    def clip_preview_dir(self) -> Path:
        return self.version_dir / "clip_previews"

    @property
    def hdri_dir(self) -> Path:
        """Extracted sky HDRIs: one folder per sky, beside the sky catalogue."""
        return self.version_dir / "hdri"

    def texture_dir(self, path: str, raw: bool = False) -> Path:
        key = f"{path}|raw" if raw else path
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "textures" / digest

    @property
    def map_root(self) -> Path:
        """Built maps live beside the version folders, so a game update does not strand them."""
        return self.root / "maps"

    def map_dir(self, name: str) -> Path:
        return self.map_root / name

    def stale_dirs(self) -> list[Path]:
        """Folders left behind by other game versions or converter builds."""
        if not self.cache_dir.is_dir():
            return []
        return sorted(p for p in self.cache_dir.iterdir() if p.is_dir() and p != self.version_dir)


def without_maps(folders: list[Path], map_root: Path) -> tuple[list[Path], list[Path]]:
    """Split version folders into what can be deleted and the built maps inside them.

    Saved files may link to a built map, so before a folder goes its maps are
    moved to the stable map folder. Returns (folders and files to delete, map
    folders that could not be moved and are kept).
    """
    delete, kept = [], []
    for folder in folders:
        maps = folder / "maps"
        if not maps.is_dir():
            delete.append(folder)
            continue
        left = []
        for old in [m for m in maps.iterdir() if m.is_dir()]:
            if not maplib.move_map(old, Path(map_root) / old.name):
                left.append(old)
        if left:
            kept += left
            delete += [p for p in folder.iterdir() if p != maps]
        else:
            delete.append(folder)
    return delete, kept


def folder_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())