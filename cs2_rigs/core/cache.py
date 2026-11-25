"""On-disk cache of converted assets, keyed to the game version and converter build. No Blender imports here."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

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
    def finishes_file(self) -> Path:
        return self.version_dir / "finishes.json"

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

    def texture_dir(self, path: str, raw: bool = False) -> Path:
        key = f"{path}|raw" if raw else path
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "textures" / digest

    def map_dir(self, name: str) -> Path:
        return self.version_dir / "maps" / name

    def stale_dirs(self) -> list[Path]:
        """Folders left behind by other game versions or converter builds."""
        if not self.cache_dir.is_dir():
            return []
        return sorted(p for p in self.cache_dir.iterdir() if p.is_dir() and p != self.version_dir)


def without_maps(folders: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split version folders into what can be deleted and the built maps inside them.

    Saved files may link to a built map, so clearing old versions keeps the maps.
    Returns (folders and files to delete, map folders kept).
    """
    delete, kept = [], []
    for folder in folders:
        maps = folder / "maps"
        if not maps.is_dir():
            delete.append(folder)
            continue
        kept += [m for m in maps.iterdir() if m.is_dir()]
        delete += [p for p in folder.iterdir() if p != maps]
    return delete, kept


def folder_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())