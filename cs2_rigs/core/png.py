"""Write 8-bit PNG files from float images, without Blender, so it can be done off the main thread."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import numpy as np

_COLOUR_TYPES = {1: 0, 2: 4, 3: 2, 4: 6}  # channels -> PNG colour type (grey, grey+alpha, RGB, RGBA)
_LEVEL = 1  # compression; level 1 is several times faster than 6 and the files are only a little larger
_SUB = 1  # the Sub filter, which predicts each byte from the one a pixel further left


def to_bytes(values) -> np.ndarray:
    """Values in [0, 1] as bytes, rounded the way Blender stores a float written to a byte image."""
    return np.floor(np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)).astype(np.uint8)


def _filtered_rows(pixels: np.ndarray, channels: int) -> np.ndarray:
    """The image rows with the Sub filter: each byte minus the same channel of the pixel left of it."""
    height, width, _ = pixels.shape
    flat = pixels.reshape(height, width * channels).astype(np.int16)
    filtered = flat.copy()
    filtered[:, channels:] = flat[:, channels:] - flat[:, :-channels]
    rows = np.empty((height, 1 + width * channels), dtype=np.uint8)
    rows[:, 0] = _SUB
    rows[:, 1:] = (filtered & 0xFF).astype(np.uint8)
    return rows


def encode(values) -> bytes:
    """A (height, width[, channels]) float array, top row first, as PNG file contents."""
    pixels = to_bytes(values)
    if pixels.ndim == 2:
        pixels = pixels[..., None]
    height, width, channels = pixels.shape
    rows = _filtered_rows(pixels, channels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, _COLOUR_TYPES[channels], 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows.tobytes(), _LEVEL)) + chunk(b"IEND", b"")


def write(path: Path, values) -> None:
    """Write a PNG, replacing any file there only once it is complete."""
    path = Path(path)
    partial = path.with_name(f"{path.name}.{os.getpid()}.part")
    partial.write_bytes(encode(values))
    os.replace(partial, path)
