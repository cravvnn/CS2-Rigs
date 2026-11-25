"""Find a newer CS2 Rigs release on GitHub and fetch it. No Blender imports here.

Nothing here installs anything: the add-on asks first, then hands the
downloaded zip to Blender's own extension installer.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import tomllib
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

RELEASES_URL = "https://api.github.com/repos/cravvnn/CS2-Rigs/releases?per_page=20"
ASSET_RE = re.compile(r"^CS2_Rigs_(?P<version>[^_]+)_Windows_x64\.zip$")
EXTENSION_ID = "cs2_rigs"
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([a-z]+)\.?(\d*))?$")
_STAGES = {"alpha": 0, "beta": 1, "rc": 2}  # a version without a stage is a full release, above them all


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str  # "0.2.1-alpha"
    page: str  # the release page on GitHub
    asset_url: str
    asset_name: str


def version_key(version: str) -> tuple[int, ...]:
    """A version as something that sorts: 0.2.0-alpha < 0.2.0-beta < 0.2.0 < 0.2.1-alpha."""
    match = _VERSION_RE.match(version.strip())
    if match is None:
        raise ValueError(f"not a version: {version!r}")
    major, minor, patch, stage, number = match.groups()
    rank = _STAGES.get(stage, -1) if stage else len(_STAGES)
    if rank < 0:
        raise ValueError(f"unknown release stage in {version!r}")
    return int(major), int(minor), int(patch), rank, int(number or 0)


def newest_release(releases: list[dict]) -> Release | None:
    """The newest published release that carries an add-on zip, pre-releases included."""
    found = []
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            match = ASSET_RE.match(asset.get("name", ""))
            if match is None:
                continue
            try:
                key = version_key(match["version"])
            except ValueError:
                continue
            found.append((key, Release(match["version"], release.get("html_url", ""), asset["browser_download_url"], asset["name"])))
    return max(found, key=lambda item: item[0])[1] if found else None


def is_newer(release: Release | None, installed: str) -> bool:
    if release is None:
        return False
    try:
        return version_key(release.version) > version_key(installed)
    except ValueError:
        return False


def _get(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": "cs2-rigs", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_newest(timeout: float = 10.0) -> Release | None:
    """Ask GitHub for the newest release."""
    try:
        with _get(RELEASES_URL, timeout) as response:
            releases = json.load(response)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
        raise UpdateError(f"Could not check for updates: {exc}") from exc
    if not isinstance(releases, list):
        raise UpdateError("Could not check for updates: GitHub sent something unexpected.")
    return newest_release(releases)


def download(release: Release, folder: Path, timeout: float = 60.0) -> Path:
    """Download a release's zip into `folder` and check it is the add-on at that version."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / release.asset_name
    partial = path.with_name(path.name + ".part")
    try:
        with _get(release.asset_url, timeout) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        partial.replace(path)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Could not download {release.asset_name}: {exc}") from exc
    check_package(path, release.version)
    return path


def check_package(path: Path, version: str) -> None:
    """The zip must be this add-on, at the version the release says."""
    try:
        with zipfile.ZipFile(path) as bundle:
            manifest = tomllib.loads(bundle.read("blender_manifest.toml").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UpdateError(f"{path.name} is not a CS2 Rigs package.") from exc
    if manifest.get("id") != EXTENSION_ID or manifest.get("version") != version:
        raise UpdateError(f"{path.name} is not CS2 Rigs {version}.")
