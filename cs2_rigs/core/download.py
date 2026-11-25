"""Download the pinned Source2Viewer-CLI release. No Blender imports here.

The version is pinned, and the archive is checked against a known SHA-256 before
anything is extracted, so an add-on update is what moves users to a new converter.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .converter import CLI_NAME

CLI_VERSION = "21.1"
_RELEASE_URL = "https://github.com/ValveResourceFormat/ValveResourceFormat/releases/download"
# asset name -> SHA-256, from the release's published digests
_ASSETS = {
    "win32": ("cli-windows-x64.zip", "cd01665e4d31d70c1081546a697eb5654f30718000fef1a2bb0654ae044d659e"),
    "linux": ("cli-linux-x64.zip", "6c76b646485977480b9f25bcf5026c30856f70e5d138a1eb127b52d768156843"),
}


class DownloadError(RuntimeError):
    pass


def cli_dir(tools_dir: Path) -> Path:
    return tools_dir / f"source2viewer-{CLI_VERSION}"


def installed_cli(tools_dir: Path) -> Path | None:
    path = cli_dir(tools_dir) / CLI_NAME
    # An empty file is what an interrupted copy can leave; it would only fail later.
    return path if path.is_file() and path.stat().st_size > 0 else None


def asset_for_platform(platform: str = sys.platform) -> tuple[str, str, str]:
    """Return (url, file name, sha256) for this platform."""
    key = "linux" if platform.startswith("linux") else platform
    if key not in _ASSETS:
        raise DownloadError(f"There is no converter download for this platform ({platform}).")
    name, digest = _ASSETS[key]
    return f"{_RELEASE_URL}/{CLI_VERSION}/{name}", name, digest


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_verified(archive: Path, expected_sha256: str, destination: Path) -> None:
    """Check the archive's hash, then extract it into `destination` atomically."""
    actual = sha256_of(archive)
    if actual != expected_sha256:
        raise DownloadError("The converter download is damaged or not the expected file. Try again.")
    staging = destination.with_name(f"{destination.name}.{os.getpid()}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            root = staging.resolve()
            for member in bundle.infolist():
                target = (staging / member.filename).resolve()
                if root != target and root not in target.parents:
                    raise DownloadError("The converter archive contains an unsafe path.")
            bundle.extractall(staging)
        if not (staging / CLI_NAME).is_file():
            raise DownloadError(f"The converter archive does not contain {CLI_NAME}.")
        if sys.platform != "win32":
            os.chmod(staging / CLI_NAME, 0o755)
        shutil.rmtree(destination, ignore_errors=True)
        staging.replace(destination)
    except zipfile.BadZipFile as exc:
        raise DownloadError("The converter download is not a valid archive. Try again.") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def download_cli(tools_dir: Path, timeout: float = 60.0) -> Path:
    """Download and unpack the pinned converter; return the path to the executable."""
    existing = installed_cli(tools_dir)
    if existing is not None:
        return existing
    url, name, digest = asset_for_platform()
    archive = tools_dir / f"{name}.{os.getpid()}.download"
    try:
        tools_dir.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "cs2-rigs"})
        with urllib.request.urlopen(request, timeout=timeout) as response, open(archive, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        extract_verified(archive, digest, cli_dir(tools_dir))
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        raise DownloadError(f"Could not download the converter: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    return cli_dir(tools_dir) / CLI_NAME
