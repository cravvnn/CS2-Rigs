import struct
import zlib

import numpy as np

from core import png


def _decode(data: bytes) -> np.ndarray:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    at, chunks = 8, {}
    while at < len(data):
        (length,) = struct.unpack(">I", data[at:at + 4])
        kind = data[at + 4:at + 8]
        chunks[kind] = chunks.get(kind, b"") + data[at + 8:at + 8 + length]
        assert struct.unpack(">I", data[at + 8 + length:at + 12 + length])[0] == zlib.crc32(kind + data[at + 8:at + 8 + length])
        at += 12 + length
    width, height, depth, colour, *_ = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    channels = {0: 1, 4: 2, 2: 3, 6: 4}[colour]
    assert depth == 8
    rows = np.frombuffer(zlib.decompress(chunks[b"IDAT"]), dtype=np.uint8).reshape(height, 1 + width * channels)
    pixels = rows[:, 1:].astype(np.int16)
    filters = rows[:, 0]
    assert set(filters.tolist()) <= {0, 1}
    for y in np.flatnonzero(filters == 1):
        row = pixels[y]
        for x in range(channels, len(row)):
            row[x] = (row[x] + row[x - channels]) & 0xFF
    return pixels.astype(np.uint8).reshape(height, width, channels)


def test_floats_round_to_bytes_like_blender_and_keep_their_channels(tmp_path):
    values = np.array([[[0.0, 0.5, 1.0, 0.2], [1.5, -1.0, 0.0019, 0.998]]], dtype=np.float32)
    assert png.to_bytes(values).tolist() == [[[0, 128, 255, 51], [255, 0, 0, 254]]]
    for channels in (3, 4):
        path = tmp_path / f"out{channels}.png"
        png.write(path, values[..., :channels])
        assert _decode(path.read_bytes()).tolist() == png.to_bytes(values[..., :channels]).tolist()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out3.png", "out4.png"]  # nothing half-written left over


def test_rows_use_the_sub_filter_and_decode_to_the_same_pixels():
    values = np.linspace(0.0, 1.0, 3 * 5 * 4, dtype=np.float32).reshape(3, 5, 4)
    data = png.encode(values)
    at = 8 + 12 + struct.unpack(">I", data[8:12])[0]  # past the signature and IHDR
    (length,) = struct.unpack(">I", data[at:at + 4])
    rows = np.frombuffer(zlib.decompress(data[at + 8:at + 8 + length]), dtype=np.uint8).reshape(3, 1 + 5 * 4)
    assert rows[:, 0].tolist() == [1, 1, 1]
    # The first pixel stays as it is; every later byte is the difference from the byte four back.
    expected = png.to_bytes(values).reshape(3, 20)
    assert rows[0, 1:5].tolist() == expected[0, :4].tolist()
    assert rows[0, 5:9].tolist() == ((expected[0, 4:8].astype(int) - expected[0, 0:4]) & 0xFF).tolist()
    assert _decode(data).tolist() == expected.reshape(3, 5, 4).tolist()
