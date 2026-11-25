"""Locate the Counter-Strike 2 installation. No Blender imports here."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CS2_APP_ID = "730"
INSTALL_DIR_NAME = "Counter-Strike Global Offensive"
_PATH_RE = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class GameInstall:
    root: Path  # .../common/Counter-Strike Global Offensive

    @property
    def content_dir(self) -> Path:
        return self.root / "game" / "csgo"

    @property
    def main_package(self) -> Path:
        return self.content_dir / "pak01_dir.vpk"

    @property
    def maps_dir(self) -> Path:
        return self.content_dir / "maps"

    def version(self) -> str:
        """Game version used to key the cache, e.g. '1.41.9.6-2000942'."""
        info = read_steam_inf(self.content_dir / "steam.inf")
        patch = info.get("PatchVersion", "unknown")
        client = info.get("ClientVersion", "0")
        return f"{patch}-{client}"

    def is_valid(self) -> bool:
        return self.main_package.is_file()


def read_steam_inf(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def parse_library_paths(vdf_text: str) -> list[Path]:
    return [Path(m.group(1).replace("\\\\", "\\")) for m in _PATH_RE.finditer(vdf_text)]


def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        import winreg

        for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
            except OSError:
                pass
        roots.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam")
    else:
        home = Path.home()
        roots += [
            home / ".local/share/Steam",
            home / ".steam/steam",
            home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        ]
    seen: set[str] = set()
    unique = []
    for root in roots:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def find_install(override: str | os.PathLike | None = None) -> GameInstall | None:
    """Return the CS2 install, preferring an explicit folder when given."""
    if override:
        # Also accept the 'game' or 'game/csgo' folder inside the install.
        folder = Path(override)
        roots = [folder]
        if folder.name.lower() == "game":
            roots.append(folder.parent)
        elif folder.name.lower() == "csgo" and folder.parent.name.lower() == "game":
            roots.append(folder.parent.parent)
        return next((GameInstall(root) for root in roots if GameInstall(root).is_valid()), None)
    for steam in steam_roots():
        libraries = [steam]
        try:
            vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            libraries += parse_library_paths(vdf)
        except OSError:
            pass
        for library in libraries:
            install = GameInstall(library / "steamapps" / "common" / INSTALL_DIR_NAME)
            if install.is_valid():
                return install
    return None
