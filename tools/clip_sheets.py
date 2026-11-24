"""Draw contact sheets of clip previews as small PNGs, for checking a sweep by eye.

    python tools/clip_sheets.py <out_dir> [cache_version_dir]

One cell per clip: the stick figure from the front (left) and the side (right),
at the clip's start (dim), middle and end (bright). Left bones are teal, right
bones orange. A red frame marks a clip the sweep flagged, a yellow one a clip
with a note worth a look (bones that change length). Sheets cover one clip per
folder, then every flagged or noted clip; index.txt names the clip in each cell.
"""

import json
import os
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cs2_rigs"))
from core import clipdata  # noqa: E402

CELL_W, CELL_H = 150, 130
COLUMNS, ROWS = 8, 6
BACKGROUND = (24, 26, 30)
COLOURS = {"L": (64, 200, 190), "R": (240, 150, 60), "C": (235, 235, 235)}
FLAG = (220, 50, 50)
NOTED = (220, 200, 60)
NOTE_WORDS = ("bone lengths",)
FINE = {"ok", "other-skeleton", "unchecked"}
DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
    "8": "111101111101111", "9": "111101111001111",
}


class Image:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(BACKGROUND) * width * height)

    def dot(self, x, y, colour):
        if 0 <= x < self.width and 0 <= y < self.height:
            i = 3 * (y * self.width + x)
            self.pixels[i:i + 3] = bytes(colour)

    def line(self, x0, y0, x1, y1, colour):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error = dx + dy
        while True:
            self.dot(x0, y0, colour)
            if x0 == x1 and y0 == y1:
                return
            twice = 2 * error
            if twice >= dy:
                error += dy
                x0 += sx
            if twice <= dx:
                error += dx
                y0 += sy

    def box(self, x0, y0, x1, y1, colour):
        for x, y, x2, y2 in ((x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)):
            self.line(x, y, x2, y2, colour)

    def number(self, x, y, text, colour):
        for k, char in enumerate(text):
            for j, bit in enumerate(DIGITS.get(char, "")):
                if bit == "1":
                    for a in range(2):
                        for b in range(2):
                            self.dot(x + 8 * k + 2 * (j % 3) + a, y + 2 * (j // 3) + b, colour)

    def save(self, path: Path):
        raw = b"".join(b"\x00" + bytes(self.pixels[3 * self.width * y: 3 * self.width * (y + 1)]) for y in range(self.height))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def draw_cell(image, left, top, preview, frame_colour, number):
    bones, parents, sides = preview["bones"], preview["parents"], preview["side"]
    frames = preview["frames"]
    picks = [(frames[0], 0.35), (frames[len(frames) // 2], 0.6), (frames[-1], 1.0)]
    pelvis = bones.index("pelvis") if "pelvis" in bones else 0
    origin = frames[0][3 * pelvis: 3 * pelvis + 3]
    scale = (CELL_H - 20) / 2.0  # two metres fill the cell
    half = CELL_W // 2
    for frame, strength in picks:
        for view in (0, 1):  # front: x across; side: y across
            centre = left + half // 2 + view * half
            for i, parent in enumerate(parents):
                if parent < 0:
                    continue
                colour = tuple(int(c * strength + b * (1 - strength)) for c, b in zip(COLOURS[sides[i]], BACKGROUND))
                a, b = frame[3 * i: 3 * i + 3], frame[3 * parent: 3 * parent + 3]
                across = 0 if view == 0 else 1
                image.line(
                    centre + (a[across] - origin[across]) * scale, top + CELL_H - 6 - a[2] * scale,
                    centre + (b[across] - origin[across]) * scale, top + CELL_H - 6 - b[2] * scale, colour,
                )
    image.line(left + half, top + 14, left + half, top + CELL_H - 4, (60, 62, 68))
    image.number(left + 4, top + 3, str(number), (150, 150, 150))
    if frame_colour:
        image.box(left + 1, top + 1, left + CELL_W - 2, top + CELL_H - 2, frame_colour)


def main():
    out_dir = Path(sys.argv[1])
    version_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else next(
        (Path(os.environ["LOCALAPPDATA"]) / "cs2-rigs" / "cache").iterdir())
    status = json.loads((version_dir / "clip_status.json").read_text(encoding="utf-8"))["clips"]
    preview_dir = version_dir / "clip_previews"
    picked, seen = [], set()
    for clip, entry in sorted(status.items()):
        folder = clipdata.folder_of(clip)
        if folder not in seen and entry["verdict"] == "ok":
            seen.add(folder)
            picked.append(clip)
    flagged = [clip for clip, entry in sorted(status.items()) if entry["verdict"] not in FINE]
    noted = [clip for clip, entry in sorted(status.items()) if any(word in entry["note"] for word in NOTE_WORDS)]
    picked += [clip for clip in flagged + noted if clip not in picked]
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("sheet_*.png"):
        old.unlink()
    index_lines = []
    per_sheet = COLUMNS * ROWS
    drawn = 0
    cells = [(clip, clipdata.read_preview(preview_dir, clip)) for clip in picked]
    missing = [clip for clip, preview in cells if preview is None]
    cells = [(clip, preview) for clip, preview in cells if preview is not None]
    for sheet in range(0, len(cells), per_sheet):
        page = cells[sheet: sheet + per_sheet]
        image = Image(COLUMNS * CELL_W, -(-len(page) // COLUMNS) * CELL_H)
        name = f"sheet_{sheet // per_sheet + 1:02d}.png"
        for k, (clip, preview) in enumerate(page):
            drawn += 1
            entry = status[clip]
            colour = FLAG if entry["verdict"] not in FINE else NOTED if clip in noted else None
            draw_cell(image, (k % COLUMNS) * CELL_W, (k // COLUMNS) * CELL_H, preview, colour, drawn)
            index_lines.append(f"{name} {drawn:4d} {entry['verdict']:15} {clipdata.category(clip):22} {clip.removeprefix(clipdata.CLIP_PREFIX)}  {entry['note']}")
        image.save(out_dir / name)
    for clip in missing:
        index_lines.append(f"(no preview)      {status[clip]['verdict']:15} {clip.removeprefix(clipdata.CLIP_PREFIX)}  {status[clip]['note']}")
    (out_dir / "index.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    print(f"{drawn} clips on {(drawn + per_sheet - 1) // per_sheet} sheets, {len(missing)} flagged clips without a preview, in {out_dir}")


if __name__ == "__main__":
    main()
