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

    def model_dir(self, model_key: str) -> Path:
        return self.version_dir / "models" / model_key.replace("/", "__")

    def clip_dir(self, model_key: str, clip: str) -> Path:
        digest = hashlib.sha1(clip.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "clips" / model_key.replace("/", "__") / digest

    def texture_dir(self, path: str) -> Path:
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
        return self.version_dir / "textures" / digest

    def map_dir(self, name: str) -> Path:
        return self.version_dir / "maps" / name

    def stale_dirs(self) -> list[Path]:
        """Folders left behind by other game versions or converter builds."""
        if not self.cache_dir.is_dir():
            return []
        return sorted(p for p in self.cache_dir.iterdir() if p.is_dir() and p != self.version_dir)


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())