"""Write 8-bit PNG files from float images, without Blender, so it can be done off the main thread."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import numpy as np

_COLOUR_TYPES = {1: 0, 2: 4, 3: 2, 4: 6}  # channels -> PNG colour type (grey, grey+alpha, RGB, RGBA)


def to_bytes(values) -> np.ndarray:
    """Values in [0, 1] as bytes, rounded the way Blender stores a float written to a byte image."""
    return np.floor(np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)).astype(np.uint8)


def encode(values) -> bytes:
    """A (height, width[, channels]) float array, top row first, as PNG file contents."""
    pixels = to_bytes(values)
    if pixels.ndim == 2:
        pixels = pixels[..., None]
    height, width, channels = pixels.shape
    rows = np.zeros((height, 1 + width * channels), dtype=np.uint8)  # each row starts with filter 0 (none)
    rows[:, 1:] = pixels.reshape(height, width * channels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, _COLOUR_TYPES[channels], 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)) + chunk(b"IEND", b"")


def write(path: Path, values) -> None:
    """Write a PNG, replacing any file there only once it is complete."""
    path = Path(path)
    partial = path.with_name(f"{path.name}.{os.getpid()}.part")
    partial.write_bytes(encode(values))
    os.replace(partial, path)
