"""Wrapper around Source2Viewer-CLI (ValveResourceFormat, MIT). No Blender imports here.

Facts this wrapper relies on, checked against CS2 1.41.9.6 with CLI 21.1:
- An agent exported WITHOUT animations has no skeleton at all, so every model
  export asks for at least one clip.
- Clips are standalone files in the package; the CLI resolves them onto the
  model. A clip's glTF animation name is its package path without extension.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import proc

CLI_NAME = "Source2Viewer-CLI.exe" if sys.platform == "win32" else "Source2Viewer-CLI"
ENV_CLI = "CS2RIG_S2V_CLI"
EXPORT_TIMEOUT = 1800
STAGING_MARK = ".partial-"  # what 0.2.x called its staging folders, still cleaned up
STAGING_PREFIX = "s"  # this version's staging folders are '.s<id>': a short path adds no length
STAGING_MAX_AGE = 2 * EXPORT_TIMEOUT  # seconds before a staging folder counts as abandoned


class ConverterError(RuntimeError):
    pass


def failure_message(what: str, output: str, lines: int = 8) -> str:
    """A failure with the tool's last words on the first line, where Blender's status bar shows them."""
    tail = [line.strip() for line in output.strip().splitlines() if line.strip()][-lines:]
    if not tail:
        return f"{what}, without saying why."
    return f"{what}: {tail[-1]}" + ("\n" + "\n".join(tail[:-1]) if len(tail) > 1 else "")


@dataclass(frozen=True)
class PackageEntry:
    path: str
    size: int


def find_cli(override: str | os.PathLike | None = None) -> Path | None:
    """A converter the user pointed at, through the preferences or the environment."""
    for candidate in (override, os.environ.get(ENV_CLI)):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir():
            path = path / CLI_NAME
        if path.is_file():
            return path
    return None


def parse_listing(text: str) -> list[PackageEntry]:
    """Parse `--vpk_list` lines: 'some/path.vmdl_c CRC:00d9a1dcca size:563655'."""
    entries = []
    for line in text.splitlines():
        head, sep, tail = line.rpartition(" CRC:")
        if not sep:
            continue
        _, _, size = tail.partition("size:")
        try:
            entries.append(PackageEntry(head.strip(), int(size.strip())))
        except ValueError:
            continue
    return entries


def exported_path(out_dir: Path, path: str, suffix: str = ".glb") -> Path:
    """Where the converter writes 'a/b.vmdl_c', 'maps/x.vmap_c' or 'a/t.vtex_c' under `out_dir`."""
    return out_dir / Path(path.removesuffix("_c")).with_suffix(suffix)

WINDOWS_MAX_PATH = 259  # characters; Blender cannot open longer paths even with Windows long paths on


def _too_long(path: Path) -> str:
    """A path Windows cannot open, said plainly, before any work is done."""
    return (
        f"The path for {path.name} is {len(str(path))} characters, longer than Windows allows ({WINDOWS_MAX_PATH}). "
        "Choose a shorter Cache Folder in the add-on preferences, such as C:\\cs2-rigs."
    )


def _missing_output(result: Path, path: str, package: Path) -> str:
    """Why a conversion left no file: usually a path too long for Windows, otherwise the file is not in the package."""
    if sys.platform == "win32" and len(str(result)) > WINDOWS_MAX_PATH:
        return _too_long(result)
    return f"The converter finished but did not write {result.name}. Is '{path}' in {package.name}?"


def _remove_abandoned(parent: Path) -> None:
    """Delete staging folders a killed run left behind, once they are old enough to be safe."""
    patterns = (f".{STAGING_PREFIX}*", f"*{STAGING_MARK}*")
    for pattern in patterns:
        for old in parent.glob(pattern):
            try:
                abandoned = time.time() - old.stat().st_mtime > STAGING_MAX_AGE
            except OSError:
                continue
            if abandoned and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)


class Converter:
    def __init__(self, cli: Path):
        self.cli = Path(cli)

    def tag(self) -> str:
        """Identifies this converter build; exports from different builds are kept apart."""
        try:
            stat = self.cli.stat()
        except OSError:
            return "unknown"
        return hashlib.sha1(f"{stat.st_size}-{stat.st_mtime_ns}".encode()).hexdigest()[:8]

    def _run(self, args: list[str], timeout: float) -> str:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                [str(self.cli), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            raise ConverterError(f"Could not start the converter: {exc}") from exc
        proc.track(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill_tree(process)
            process.communicate()
            raise ConverterError(f"The converter did not finish within {timeout:.0f} s.") from exc
        finally:
            proc.discard(process)
        if process.returncode != 0:
            raise ConverterError(failure_message("The converter failed", (stdout or "") + (stderr or "")))
        return stdout or ""

    def list_package(self, package: Path, path_filter: str = "", extensions: str = "") -> list[PackageEntry]:
        args = ["-i", str(package), "--vpk_list"]
        if path_filter:
            args += ["-f", path_filter]
        if extensions:
            args += ["-e", extensions]
        return parse_listing(self._run(args, timeout=300))

    def read_text(self, package: Path, path: str, work_dir: Path) -> str:
        """The contents of one plain text file inside a package."""
        self._run(["-i", str(package), "-o", str(work_dir), "-f", path], timeout=300)
        try:
            return (work_dir / path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise ConverterError(f"'{path}' could not be read from {package.name}: {exc}") from exc

    def dump(self, package: Path, path: str) -> str:
        """Everything the converter can print about one resource, as text."""
        return self._run(["-i", str(package), "-f", path, "-a"], timeout=300)

    def dump_block(self, package: Path, path: str, block: str) -> str:
        """One named block (such as DATA) of a compiled resource, as text."""
        return self._run(["-i", str(package), "-f", path, "-b", block], timeout=300)

    def stream(self, args: list[str], timeout: float):
        """Run the converter and yield its output line by line, for dumps too large to hold."""
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            process = subprocess.Popen(
                [str(self.cli), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            raise ConverterError(f"Could not start the converter: {exc}") from exc
        proc.track(process)
        started = time.monotonic()
        try:
            for line in process.stdout:
                if time.monotonic() - started > timeout:
                    raise ConverterError(f"The converter did not finish within {timeout:.0f} s.")
                yield line
            if process.wait(timeout=60) != 0:
                raise ConverterError(f"The converter failed with exit code {process.returncode}.")
        finally:
            if process.poll() is None:
                proc.kill_tree(process)
            proc.discard(process)
            process.stdout.close()

    def export_clip(self, package: Path, clip: str, out_dir: Path, compose_additive: bool = False) -> Path:
        """Export one animation clip on its own skeleton to .glb.

        A clip exported with a model is found through the model's animation graphs,
        and clips no graph uses any more come out empty. Exported alone, every clip
        converts, and its bone tracks are the same as on the model.
        """
        options = ["--gltf_export_format", "glb"]
        if compose_additive:
            options.append("--gltf_compose_additive")
        return self._convert(package, clip + ".vnmclip_c", out_dir, ".glb", options)

    def export_clip_folder(self, package: Path, folder: str, out_dir: Path, compose_additive: bool = False) -> None:
        """Export every clip under a folder in one run, much faster than one run per clip."""
        args = ["-i", str(package), "-o", str(out_dir), "-d", "-f", folder.rstrip("/") + "/", "-e", "vnmclip_c", "--gltf_export_format", "glb"]
        if compose_additive:
            args.append("--gltf_compose_additive")
        self._run(args, timeout=EXPORT_TIMEOUT)

    def dump_folder(self, package: Path, folder: str, extension: str) -> str:
        """The data block of every resource of one type under a folder, as one text."""
        return self._run(["-i", str(package), "-f", folder, "-e", extension, "-b", "DATA"], timeout=600)

    def _convert(self, package: Path, path: str, out_dir: Path, suffix: str, options: list[str]) -> Path:
        """Decompile one resource into `out_dir`; return the converted file.

        A finished conversion is reused. A new one is written to a staging folder and
        moved into place only when the converter succeeds, so an interrupted run
        never leaves a half-written file that looks finished. The staging folder's
        name is short: the path already has to fit Windows, and the old name added
        22 characters to it.
        """
        result = exported_path(out_dir, path, suffix)
        if result.is_file():
            return result
        if sys.platform == "win32" and len(str(result)) > WINDOWS_MAX_PATH:
            raise ConverterError(_too_long(result))
        # Staging is per process and thread, so two Blenders converting the same thing cannot collide.
        staging = out_dir.parent / proc.staging_name(STAGING_PREFIX)
        try:
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            _remove_abandoned(out_dir.parent)
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            self._run(["-i", str(package), "-o", str(staging), "-d", "-f", path, *options], timeout=EXPORT_TIMEOUT)
            staged = exported_path(staging, path, suffix)
            if not staged.is_file():
                raise ConverterError(_missing_output(result, path, package))
            try:
                staging.replace(out_dir)
            except OSError:
                if result.is_file():
                    pass  # another Blender finished the same conversion first
                else:
                    # The folder was there but held no finished file for us; add ours to it
                    # rather than deleting whatever another Blender may be writing.
                    shutil.copytree(staging, out_dir, dirs_exist_ok=True)
                    if not result.is_file():
                        raise
        except OSError as exc:
            raise ConverterError(f"Could not store the converted files in {out_dir}: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return result

    def export(self, package: Path, path: str, out_dir: Path, clips: tuple[str, ...] = (), materials: bool = True) -> Path:
        """Export one compiled model or map to .glb in `out_dir`; return the .glb path."""
        options = ["--gltf_export_format", "glb"]
        if clips:
            options += ["--gltf_export_animations", "--gltf_animation_list", ",".join(clips)]
        if materials:
            options += ["--gltf_export_materials", "--gltf_textures_adapt"]
        return self._convert(package, path, out_dir, ".glb", options)

    def export_texture(self, package: Path, path: str, out_dir: Path, suffix: str = ".png", raw: bool = False) -> Path:
        """Convert one compiled texture in `out_dir`; return the image. Float textures come out as .exr.

        Set `raw` to keep the stored channels untouched (needed for HemiOctAnisoRoughness textures
        that Source2Viewer's automatic decoder otherwise misinterprets).
        """
        options = ["--texture_decode_flags", "none"] if raw else []
        return self._convert(package, path, out_dir, suffix, options)

    def export_model(self, package: Path, model_path: str, out_dir: Path, clips: list[str], materials: bool) -> Path:
        """Export a model with the named clips. A model exported without a clip has no skeleton."""
        if not clips:
            raise ConverterError("A model export needs at least one clip, or it has no skeleton.")
        return self.export(package, model_path, out_dir, tuple(clips), materials)