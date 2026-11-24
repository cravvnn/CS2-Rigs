"""Browse every game clip in a window drawn over the 3D View, with a looping skeleton preview.

core/clipbrowse.py works out the tree, list, layout and preview poses; this module draws them
with gpu and blf and runs the modal operator that reacts to the mouse and keyboard.
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

from . import importer, session
from .core import catalog as catalog_mod
from .core import clipbrowse as cb
from .core import log as log_mod


def _hex(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    value = value.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255, alpha)


COLORS = {
    "shade": (0.0, 0.0, 0.0, 0.45),
    # Greys and the list highlight of Blender's default theme, so the browser sits in with the rest of the UI.
    "panel": _hex("2b2b2b"),
    "header": _hex("303030"),
    "pane": _hex("232323"),
    "rule": _hex("3d3d3d"),
    "hover": _hex("383838"),
    "selected": _hex("334d80"),
    "field": _hex("1d1d1d"),
    "badge": _hex("ffffff", 0.06),
    "bar": _hex("5a5a5a"),
    "bar-hot": _hex("808080"),
    "text": _hex("e6e6e6"),
    "dim": _hex("999999"),
    "faint": _hex("6e6e6e"),
    "ct": _hex("6d8ccc"),  # CS2 CT blue, lifted a little to read on the grey panel
    "t": _hex("de9b35"),  # CS2 T gold
    "centre": _hex("f2f4f7"),
    "warn": _hex("e8a33d"),
    "error": _hex("e0684b"),
}
SIDE_COLORS = {"L": "ct", "R": "t", "C": "centre"}
# Muted, so the badges read as notes: most clips are OK, which is shown in plain grey.
VERDICT_COLORS = {
    "ok": _hex("7a7a7a"),
    "additive": _hex("8397c0"),
    "no-animation": _hex("c97a66"),
    "collapsed": _hex("c97a66"),
    "length-mismatch": _hex("c4a066"),
    "other-skeleton": _hex("8a8a8a"),
    "converter-error": _hex("c97a66"),
    "timeout": _hex("c97a66"),
    "unchecked": _hex("7a7a7a"),
}
QUIET_VERDICTS = {"ok"}  # shown as plain text, without a pill
MONO_FONT = "fonts/DejaVuSansMono.woff2"
FRAME_RATE = 30.0
PREVIEW_CACHE = 64


_fonts: dict[str, int] = {}
_fits: dict[tuple, str] = {}


def _mono_font() -> int:
    """Blender's monospace font for the clock, loaded once; the UI font if it is missing."""
    if "mono" not in _fonts:
        path = bpy.utils.system_resource("DATAFILES", path=MONO_FONT)
        font = blf.load(path) if path and Path(path).is_file() else -1
        _fonts["mono"] = font if font >= 0 else 0
    return _fonts["mono"]


class Painter:
    """Batches flat shapes into one draw per layer, with a soft edge so they look smooth; text draws after."""

    def __init__(self):
        self.shader = gpu.shader.from_builtin("SMOOTH_COLOR")
        self.font = 0
        self.mono = _mono_font()
        self.origin = gpu.state.viewport_get()[:2]
        self.scissor = gpu.state.scissor_get()  # the region's own, put back after each clipped part
        self._pos: list[tuple[float, float, float]] = []
        self._col: list[tuple[float, float, float, float]] = []
        self._text: list[tuple] = []

    # Shapes

    def polygon(self, points: list[tuple[float, float]], color, soft: float = 1.0) -> None:
        """A convex polygon, given counter-clockwise, with a one-pixel fade at its edge."""
        count = len(points)
        if count < 3:
            return
        clear = (*color[:3], 0.0)
        inner, outer = [], []
        for i in range(count):
            (ax, ay), (bx, by), (cx, cy) = points[i - 1], points[i], points[(i + 1) % count]
            n1 = _normal(ax, ay, bx, by)
            n2 = _normal(bx, by, cx, cy)
            nx, ny = n1[0] + n2[0], n1[1] + n2[1]
            length = (nx * nx + ny * ny) ** 0.5 or 1.0
            nx, ny = nx / length, ny / length
            reach = soft * 0.5 / max(nx * n1[0] + ny * n1[1], 0.35)
            inner.append((bx - nx * reach, by - ny * reach))
            outer.append((bx + nx * reach, by + ny * reach))
        for i in range(1, count - 1):
            self._tri(inner[0], inner[i], inner[i + 1], color, color, color)
        for i in range(count):
            j = (i + 1) % count
            self._tri(inner[i], outer[i], outer[j], color, clear, clear)
            self._tri(inner[i], outer[j], inner[j], color, clear, color)

    def _tri(self, a, b, c, ca, cb_, cc) -> None:
        self._pos += [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (c[0], c[1], 0.0)]
        self._col += [ca, cb_, cc]

    def rect(self, r: cb.Rect, color, radius: float = 0.0) -> None:
        if r.w <= 0 or r.h <= 0:
            return
        radius = min(radius, r.w / 2, r.h / 2)
        if radius < 1:
            points = [(r.x, r.y), (r.right, r.y), (r.right, r.top), (r.x, r.top)]
            self.polygon(points, color, soft=0.0001)
            return
        points = []
        for cx, cy, start in ((r.right - radius, r.y + radius, -90), (r.right - radius, r.top - radius, 0),
                              (r.x + radius, r.top - radius, 90), (r.x + radius, r.y + radius, 180)):
            points += _arc(cx, cy, radius, start, start + 90, 5)
        self.polygon(points, color)

    def circle(self, x: float, y: float, radius: float, color) -> None:
        self.polygon(_arc(x, y, radius, 0, 360, max(10, int(radius * 2)))[:-1], color)

    def ring(self, x: float, y: float, radius: float, width: float, color) -> None:
        steps = max(16, int(radius * 2.5))
        outer = _arc(x, y, radius + width / 2, 0, 360, steps)
        inner = _arc(x, y, radius - width / 2, 0, 360, steps)
        for i in range(steps):
            self._tri(inner[i], outer[i], outer[i + 1], color, color, color)
            self._tri(inner[i], outer[i + 1], inner[i + 1], color, color, color)

    def segment(self, a: tuple[float, float], b: tuple[float, float], width: float, color) -> None:
        """A line with round ends."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length < 0.01:
            self.circle(a[0], a[1], width / 2, color)
            return
        angle = math.degrees(math.atan2(dy, dx))
        half = width / 2
        points = _arc(b[0], b[1], half, angle - 90, angle + 90, 4) + _arc(a[0], a[1], half, angle + 90, angle + 270, 4)
        self.polygon(points, color)

    # Text

    def text(self, x: float, y: float, value: str, size: float, color, mono: bool = False) -> None:
        self._text.append((x, y, value, size, color, mono))

    def width(self, value: str, size: float, mono: bool = False) -> float:
        font = self.mono if mono else self.font
        blf.size(font, size)
        return blf.dimensions(font, value)[0]

    def fit(self, value: str, size: float, room: float, mono: bool = False) -> str:
        """Shorten text with an ellipsis to fit a width."""
        key = (value, size, int(room), mono)
        if key not in _fits:
            if len(_fits) > 4000:
                _fits.clear()
            fitted = value
            if self.width(value, size, mono) > room:
                low, high = 0, len(value)
                while low < high:
                    middle = (low + high + 1) // 2
                    if self.width(value[:middle] + "…", size, mono) <= room:
                        low = middle
                    else:
                        high = middle - 1
                fitted = value[:low] + "…" if low else ""
            _fits[key] = fitted
        return _fits[key]

    # Drawing

    def flush(self) -> None:
        if self._pos:
            gpu.state.blend_set("ALPHA")
            batch = batch_for_shader(self.shader, "TRIS", {"pos": self._pos, "color": self._col})
            self.shader.bind()
            batch.draw(self.shader)
            self._pos, self._col = [], []
        for x, y, value, size, color, mono in self._text:
            font = self.mono if mono else self.font
            blf.size(font, size)
            blf.color(font, *color)
            blf.position(font, round(x), round(y), 0)
            blf.draw(font, value)
        self._text = []

    @contextmanager
    def clipped(self, r: cb.Rect):
        """Draw only inside a rectangle, for the scrolling columns."""
        self.flush()
        gpu.state.scissor_test_set(True)
        gpu.state.scissor_set(int(self.origin[0] + r.x), int(self.origin[1] + r.y), max(0, int(r.w)), max(0, int(r.h)))
        try:
            yield
        finally:
            self.flush()
            gpu.state.scissor_set(*self.scissor)


def _normal(ax: float, ay: float, bx: float, by: float) -> tuple[float, float]:
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    return (dy / length, -dx / length)  # outward for counter-clockwise points


def _arc(cx: float, cy: float, radius: float, start: float, end: float, steps: int) -> list[tuple[float, float]]:
    return [
        (cx + radius * math.cos(math.radians(start + (end - start) * i / steps)),
         cy + radius * math.sin(math.radians(start + (end - start) * i / steps)))
        for i in range(steps + 1)
    ]


@dataclass
class Browser:
    """What the open browser shows; kept apart from the operator so it can be drawn without one."""

    clips: list[cb.Clip]
    status: dict[str, dict] | None
    preview_dir: Path | None
    family: str = ""  # the held weapon's clip family, highlighted in the tree
    weapon: str = ""  # the held weapon's key
    agent: str = ""  # name of the agent clips load onto
    open_family: bool = False  # open on the family (Only Clips for the Held Weapon)
    can_build: bool = False
    query: str = ""
    node: int = 0
    tree_scroll: float = 0.0
    list_scroll: float = 0.0
    hover: tuple[str, int] = ("outside", -1)
    dragging: tuple[str, float] | None = None  # the column whose scrollbar is held, and where on its thumb
    focus: int = -1  # list row under the mouse or chosen with the keys
    shown: cb.Clip | None = None  # the clip in the preview; stays after the mouse leaves the list
    message: str = ""
    started: float = field(default_factory=time.monotonic)
    _previews: dict[str, cb.Preview | None] = field(default_factory=dict)

    def __post_init__(self):
        self.tree = cb.build_tree(self.clips)
        self.first_folder = cb.held_folder(self.clips, self.family, self.weapon) if self.family else ""
        self.node = cb.default_node(self.tree, self.family if self.open_family else "")
        self.has_previews = bool(self.preview_dir and next(Path(self.preview_dir).glob("*.json"), None))
        self.refilter()

    def refilter(self) -> None:
        self.visible = cb.clips_in(self.clips, self.tree[self.node], self.query, self.first_folder)
        self.counts = cb.counts(self.clips, self.tree, self.query)
        self.focus = min(self.focus, len(self.visible) - 1)

    def preview(self, clip: cb.Clip) -> cb.Preview | None:
        if clip.path not in self._previews:
            if len(self._previews) >= PREVIEW_CACHE:
                self._previews.pop(next(iter(self._previews)))
            found = cb.read_preview(self.preview_dir, clip.path) if self.preview_dir else None
            self._previews[clip.path] = found
        return self._previews[clip.path]

    def show(self, clip: cb.Clip | None) -> None:
        if clip is not None and clip != self.shown:
            self.shown = clip
            self.started = time.monotonic()

    def node_label(self, clip: cb.Clip) -> str:
        """'Rifle', 'Main menu', 'First-person arms': the tree label a clip sits under."""
        for node in reversed(self.tree):
            if node.depth == 1 and node.holds(clip):
                return node.label
        return cb.folder_title(clip.group)


def draw_browser(p: Painter, b: Browser, width: float, height: float, scale: float, now: float,
                 covered: tuple[float, float, float, float] = (0, 0, 0, 0)) -> cb.Layout:
    """Draw the whole browser into a region of this size."""
    lay = cb.layout(width, height, scale, covered)
    s = lay.scale
    p.rect(cb.Rect(0, 0, width, height), COLORS["shade"])
    p.rect(cb.Rect(lay.panel.x - 2 * s, lay.panel.y - 4 * s, lay.panel.w + 4 * s, lay.panel.h + 4 * s), (0, 0, 0, 0.35), 10 * s)
    p.rect(lay.panel, COLORS["panel"], 8 * s)
    _draw_header(p, b, lay)
    _draw_tree(p, b, lay)
    _draw_search(p, b, lay, now)
    _draw_list(p, b, lay)
    _draw_preview(p, b, lay, now)
    _draw_footer(p, b, lay)
    p.flush()
    gpu.state.blend_set("NONE")
    return lay


def _baseline(r: cb.Rect, size: float) -> float:
    """Where text of this size sits to look centred in a row."""
    return r.y + (r.h - size * 0.72) / 2


def _draw_header(p: Painter, b: Browser, lay: cb.Layout) -> None:
    s, h = lay.scale, lay.header
    p.rect(h, COLORS["header"], 8 * s)
    p.rect(cb.Rect(h.x, h.y, h.w, h.h / 2), COLORS["header"])
    p.rect(cb.Rect(h.x, h.y, h.w, max(1, round(s))), COLORS["rule"])
    x = h.x + lay.pad + 4 * s
    p.text(x, _baseline(h, lay.title), "Browse Clips", lay.title, COLORS["text"])
    x += p.width("Browse Clips", lay.title) + 14 * s
    usable = sum(1 for clip in b.clips if clip.usable)
    p.text(x, _baseline(h, lay.small), f"{usable} clips for agents", lay.small, COLORS["dim"])
    if b.weapon:
        note = f"Holding {b.weapon.replace('_', ' ')}: {b.family} clips fit"
        room = lay.close.x - lay.pad
        w = p.width(note, lay.small)
        p.circle(room - w - 9 * s, h.y + h.h / 2, 3.5 * s, COLORS["t"])
        p.text(room - w, _baseline(h, lay.small), note, lay.small, COLORS["dim"])
    hot = b.hover[0] == "close"
    c = lay.close
    if hot:
        p.rect(c.inset(5 * s), COLORS["hover"], 4 * s)
    arm = 5 * s
    colour = COLORS["text"] if hot else COLORS["dim"]
    cx, cy = c.x + c.w / 2, c.y + c.h / 2
    p.segment((cx - arm, cy - arm), (cx + arm, cy + arm), 1.6 * s, colour)
    p.segment((cx - arm, cy + arm), (cx + arm, cy - arm), 1.6 * s, colour)


def _draw_tree(p: Painter, b: Browser, lay: cb.Layout) -> None:
    s, view = lay.scale, lay.tree
    with p.clipped(view):
        for index, node in enumerate(b.tree):
            r = cb.row_rect(view, lay.row, b.tree_scroll, index)
            if r.top < view.y or r.y > view.top:
                continue
            if index == b.node:
                p.rect(r, COLORS["selected"], 4 * s)
                p.rect(cb.Rect(r.x, r.y + 4 * s, 3 * s, r.h - 8 * s), COLORS["ct"], 1.5 * s)
            elif b.hover == ("tree", index):
                p.rect(r, COLORS["hover"], 4 * s)
            count = b.counts[index]
            group = node.depth == 0
            colour = COLORS["faint"] if node.dimmed else COLORS["text"] if group or count else COLORS["dim"]
            x = r.x + (8 + 14 * node.depth) * s
            number = str(count)
            number_w = p.width(number, lay.small)
            p.text(r.right - 8 * s - number_w, _baseline(r, lay.small), number, lay.small, COLORS["faint"] if not count else COLORS["dim"])
            room = r.right - 8 * s - number_w - x - 14 * s
            label = node.label.upper() if group else node.label
            size = lay.small if group else lay.text
            p.text(x, _baseline(r, size), p.fit(label, size, room), size, colour)
            if b.family and node.key == f"world/{b.family}":
                p.circle(r.right - 16 * s - number_w, r.y + r.h / 2, 3 * s, COLORS["t"])
    _scrollbar(p, b, lay, "tree", view, len(b.tree), b.tree_scroll)


def _draw_search(p: Painter, b: Browser, lay: cb.Layout, now: float) -> None:
    s, r = lay.scale, lay.search
    p.rect(r, COLORS["rule"], 5 * s)
    p.rect(r.inset(max(1, round(s))), COLORS["field"], 4 * s)
    cx, cy = r.x + 13 * s, r.y + r.h / 2 + 1 * s
    p.ring(cx, cy, 4.2 * s, 1.4 * s, COLORS["dim"])
    p.segment((cx + 3.2 * s, cy - 3.2 * s), (cx + 6.5 * s, cy - 6.5 * s), 1.6 * s, COLORS["dim"])
    x = r.x + 26 * s
    shown = f"{len(b.visible)} shown"
    shown_w = p.width(shown, lay.small)
    p.text(r.right - 8 * s - shown_w, _baseline(r, lay.small), shown, lay.small, COLORS["dim"])
    room = r.right - 16 * s - shown_w - x
    if b.query:
        text = p.fit(b.query, lay.text, room)
        p.text(x, _baseline(r, lay.text), text, lay.text, COLORS["text"])
        x += p.width(text, lay.text) + 1 * s
    else:
        p.text(x, _baseline(r, lay.text), p.fit("Type to search", lay.text, room), lay.text, COLORS["faint"])
    if now % 1.0 < 0.6:
        p.rect(cb.Rect(x, r.y + 6 * s, max(1, round(1.2 * s)), r.h - 12 * s), COLORS["ct"])


def _draw_list(p: Painter, b: Browser, lay: cb.Layout) -> None:
    s, view = lay.scale, lay.list
    node = b.tree[b.node]
    with p.clipped(view):
        if not b.visible:
            message = f"No clips match “{b.query}” here" if b.query else "No clips here"
            p.text(view.x + 10 * s, view.top - 28 * s, p.fit(message, lay.text, view.w - 20 * s), lay.text, COLORS["dim"])
        top, bottom = cb.row_at(view, lay.row, b.list_scroll, len(b.visible), view.top - 1), len(b.visible)
        for index in range(max(0, top), bottom):
            r = cb.row_rect(view, lay.row, b.list_scroll, index)
            if r.top < view.y:
                break
            clip = b.visible[index]
            if index == b.focus:
                p.rect(r, COLORS["selected"] if b.hover[0] != "list" else COLORS["hover"], 4 * s)
            if b.first_folder and clip.folder == b.first_folder:
                p.rect(cb.Rect(r.x + 2 * s, r.y + 5 * s, 2.5 * s, r.h - 10 * s), COLORS["t"], 1 * s)
            x = r.x + 10 * s
            right = r.right - 16 * s  # clear of the scrollbar
            verdict = cb.verdict(b.status, clip)
            if verdict:
                right = _badge(p, lay, right, r, verdict) - 8 * s
            name_colour = COLORS["text"] if clip.usable else COLORS["faint"]
            name = p.fit(clip.name, lay.text, right - x)
            p.text(x, _baseline(r, lay.text), name, lay.text, name_colour)
            where = clip.label[: -len(clip.name) - 1] if node.key in (cb.ALL_NODE, cb.UNUSABLE_NODE) or node.depth == 0 else clip.folder
            x += p.width(name, lay.text) + 10 * s
            if where and right - x > 30 * s:
                p.text(x, _baseline(r, lay.small), p.fit(where, lay.small, right - x), lay.small, COLORS["faint"])
    _scrollbar(p, b, lay, "list", view, len(b.visible), b.list_scroll)


def _badge(p: Painter, lay: cb.Layout, right: float, row: cb.Rect, verdict: str) -> float:
    """A small coloured pill ending at `right`; returns where it starts."""
    s = lay.scale
    text = cb.badge(verdict)
    size = round(lay.small * 0.9)
    w = p.width(text, size) + 12 * s
    h = round(16 * s)
    r = cb.Rect(right - w, row.y + (row.h - h) / 2, w, h)
    colour = VERDICT_COLORS.get(verdict, COLORS["dim"])
    if verdict not in QUIET_VERDICTS:
        p.rect(r, COLORS["badge"], h / 2)
    p.text(r.x + 6 * s, _baseline(r, size), text, size, colour)
    return r.x


def _scrollbar(p: Painter, b: Browser, lay: cb.Layout, name: str, view: cb.Rect, rows: int, scroll: float) -> None:
    bar = cb.thumb(view, lay.row, scroll, rows, lay.scale)
    if bar is None:
        return
    s = lay.scale
    hot = b.hover[0] == f"{name}-bar" or (b.dragging is not None and b.dragging[0] == name)
    w = (6 if hot else 4) * s
    p.rect(cb.Rect(view.right - w - 1 * s, bar.y, w, bar.h), COLORS["bar-hot" if hot else "bar"], w / 2)


def _draw_preview(p: Painter, b: Browser, lay: cb.Layout, now: float) -> None:
    s, pane, stage = lay.scale, lay.preview, lay.stage
    p.rect(pane, COLORS["pane"], 6 * s)
    clip = b.shown
    pad = 14 * s
    title_y = pane.top - pad - lay.title * 0.8
    if clip is None:
        message = "Hover a clip to preview it"
        p.text(stage.x + (stage.w - p.width(message, lay.text)) / 2, stage.y + stage.h / 2, message, lay.text, COLORS["dim"])
        return
    preview = b.preview(clip)
    seconds = (now - b.started) % preview.duration if preview and preview.duration > 0 else 0.0
    clock = f"{seconds:.2f} s" if preview else ""
    clock_w = p.width(clock, lay.text, mono=True) if preview else 0.0
    p.text(pane.right - pad - clock_w, title_y, clock, lay.text, COLORS["text"], mono=True)
    if preview and preview.duration > 0:
        total = f"of {preview.duration:.2f} s"
        p.text(pane.right - pad - p.width(total, lay.small, mono=True), title_y - 18 * s, total, lay.small, COLORS["faint"], mono=True)
    title = f"{b.node_label(clip)} / {clip.name}"
    room = pane.w - 2 * pad - clock_w - 12 * s
    p.text(pane.x + pad, title_y, p.fit(title, lay.title, room), lay.title, COLORS["text"])
    p.text(pane.x + pad, title_y - 18 * s, p.fit(clip.label[: -len(clip.name) - 1], lay.small, room), lay.small, COLORS["faint"])

    if preview is not None:
        _draw_skeleton(p, preview, stage, s, seconds)
    else:
        lines = ["No skeleton preview for this clip yet."]
        if not b.has_previews:
            lines.append("Previews are made by Build Clip Previews." if not b.can_build else "Make them once for every clip:")
        y = lay.button.top + 34 * s
        for line in lines:
            p.text(stage.x + (stage.w - p.width(line, lay.text)) / 2, y, line, lay.text, COLORS["dim"])
            y -= 20 * s
        if b.can_build and not b.has_previews:
            hot = b.hover[0] == "button"
            p.rect(lay.button, COLORS["ct"] if hot else COLORS["selected"], 4 * s)
            label = "Build Clip Previews"
            p.text(lay.button.x + (lay.button.w - p.width(label, lay.text)) / 2, _baseline(lay.button, lay.text), label, lay.text, COLORS["text"])

    rule_y = stage.y - 8 * s
    p.rect(cb.Rect(pane.x + pad, rule_y, pane.w - 2 * pad, max(1, round(s))), COLORS["rule"])
    caption_y = rule_y - 20 * s
    right = pane.right - pad
    verdict = cb.verdict(b.status, clip) or "unchecked"
    row = cb.Rect(pane.x, caption_y - 5 * s, pane.w, 20 * s)
    right = _badge(p, lay, right, row, verdict) - 8 * s
    caption = "Game clip / skeleton preview" if preview else "Game clip"
    if preview:
        caption += f", {len(preview.frames)} frames"
    p.text(pane.x + pad, caption_y, p.fit(caption, lay.small, right - pane.x - pad), lay.small, COLORS["dim"])
    if clip.reason:
        note = clip.reason
    elif b.status is None:
        note = "Clips have not been checked yet."
    else:
        note = str((b.status.get(clip.path) or {}).get("note") or "")
    if note:
        colour = COLORS["warn"] if clip.reason else COLORS["faint"]
        p.text(pane.x + pad, caption_y - 18 * s, p.fit(note, lay.small, pane.w - 2 * pad), lay.small, colour)


def _draw_skeleton(p: Painter, preview: cb.Preview, stage: cb.Rect, s: float, seconds: float) -> None:
    pose = preview.pose(seconds)
    points, depths, floor, travel = cb.project(preview, pose, stage)
    # The floor: a rule with ticks that slide by as root motion carries the agent along.
    p.rect(cb.Rect(stage.x, floor - s / 2, stage.w, max(1, round(s))), COLORS["rule"])
    spacing = 28 * s
    offset = (-travel) % spacing
    x = stage.x + offset
    while x < stage.right:
        p.rect(cb.Rect(x, floor - 4 * s, max(1, round(s)), 4 * s), COLORS["rule"])
        x += spacing
    root = points[preview.root]
    p.polygon(_ellipse(root[0], floor, 30 * s, 4 * s), (*COLORS["rule"][:3], 0.8), soft=5 * s)
    # Far bones first, a little darker, so crossing limbs read.
    order = sorted(range(len(points)), key=lambda i: -depths[i])
    span = (max(depths) - min(depths)) or 1.0
    for i in order:
        parent = preview.parents[i]
        colour = COLORS[SIDE_COLORS.get(preview.side[i], "centre")]
        fade = 0.7 + 0.3 * (1 - (depths[i] - min(depths)) / span)
        shade = (colour[0] * fade, colour[1] * fade, colour[2] * fade, 1.0)
        if parent >= 0:
            p.segment(points[parent], points[i], 3 * s, shade)
        p.circle(points[i][0], points[i][1], 3.4 * s, shade)
        p.circle(points[i][0], points[i][1], 1.5 * s, COLORS["pane"])
    head = preview.bones.index("head_0") if "head_0" in preview.bones else -1
    if head >= 0:
        neck = preview.parents[head]
        hx, hy = points[head]
        nx, ny = points[neck] if neck >= 0 else (hx, hy - 1)
        dx, dy = hx - nx, hy - ny
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        radius = 9 * s
        p.ring(hx + dx / length * radius * 0.9, hy + dy / length * radius * 0.9, radius, 2.2 * s, COLORS["centre"])


def _ellipse(x: float, y: float, rx: float, ry: float) -> list[tuple[float, float]]:
    return [(px, y + (py - y) * ry / rx) for px, py in _arc(x, y, rx, 0, 360, 24)[:-1]]


def _draw_footer(p: Painter, b: Browser, lay: cb.Layout) -> None:
    s, f = lay.scale, lay.footer
    p.rect(cb.Rect(f.x + lay.pad, f.top - 1, f.w - 2 * lay.pad, max(1, round(s))), COLORS["rule"])
    if b.message:
        p.text(f.x + lay.pad + 4 * s, _baseline(f, lay.small), p.fit(b.message, lay.small, f.w * 0.6), lay.small, COLORS["warn"])
    else:
        target = f"Click a clip to load it on {b.agent}" if b.agent else "Select an imported agent to load clips onto"
        p.text(f.x + lay.pad + 4 * s, _baseline(f, lay.small), p.fit(target, lay.small, f.w * 0.5), lay.small, COLORS["dim"])
    keys = "Up/Down and Enter   Wheel or drag the bar to scroll   Esc or right-click closes"
    p.text(f.right - lay.pad - 4 * s - p.width(keys, lay.small), _baseline(f, lay.small), keys, lay.small, COLORS["faint"])


# The operator.

_open: dict = {}  # the one open browser: "browser", "handler", "timer", "window", "area"


def _ui_scale(context) -> float:
    return context.preferences.system.ui_scale


def _can_build_previews() -> bool:
    try:
        bpy.ops.cs2rig.build_clip_previews.get_rna_type()
    except (AttributeError, KeyError, RuntimeError):
        return False
    return True


def _cache_file(cache, method: str, name: str) -> Path:
    """The cache's clip data paths; the clip sweep names them on Cache, otherwise they sit in the version folder."""
    accessor = getattr(cache, method, None)
    return Path(accessor()) if callable(accessor) else cache.version_dir / name


def _window_region(area):
    return next((region for region in area.regions if region.type == "WINDOW"), None)


def _covered(area, region) -> tuple[float, float, float, float]:
    """How far the toolbar, sidebar and headers reach over the 3D View when Region Overlap is on."""
    left = bottom = right = top = 0.0
    for other in area.regions:
        if other == region or other.width <= 1 or other.height <= 1:
            continue
        overlaps = other.x < region.x + region.width and region.x < other.x + other.width and             other.y < region.y + region.height and region.y < other.y + other.height
        if not overlaps:
            continue
        if other.type in {"UI", "TOOLS"}:
            if other.x + other.width / 2 > region.x + region.width / 2:
                right = max(right, region.x + region.width - other.x)
            else:
                left = max(left, other.x + other.width - region.x)
        elif other.type in {"HEADER", "TOOL_HEADER"}:
            if other.y + other.height / 2 > region.y + region.height / 2:
                top = max(top, region.y + region.height - other.y)
            else:
                bottom = max(bottom, other.y + other.height - region.y)
    return left, bottom, right, top


def _draw_callback() -> None:
    browser = _open.get("browser")
    area = bpy.context.area
    if browser is None or area is None or area.as_pointer() != _open.get("area"):
        return
    region = bpy.context.region
    draw_browser(Painter(), browser, region.width, region.height, _ui_scale(bpy.context), time.monotonic(), _covered(area, region))


def close_browser() -> None:
    """Take the browser off screen and stop its timer. Safe to call when nothing is open."""
    if not _open:
        return
    handler, timer, window = _open.get("handler"), _open.get("timer"), _open.get("window")
    _open.clear()
    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
        except ValueError:
            pass  # already removed
    if timer is not None:
        try:
            bpy.context.window_manager.event_timer_remove(timer)
        except (ReferenceError, ValueError, RuntimeError):
            pass  # the window went away with a loaded file
    for screen_window in bpy.context.window_manager.windows if window is not None else ():
        for area in screen_window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


@persistent
def _on_load_pre(*_args) -> None:
    close_browser()


_PASS = {"TIMER", "TIMER0", "TIMER1", "TIMER2", "TIMER_JOBS", "TIMER_AUTOSAVE", "TIMER_REPORT", "TIMERREGION",
         "WINDOW_DEACTIVATE", "ACTIONZONE_AREA", "ACTIONZONE_REGION", "ACTIONZONE_FULLSCREEN", "XR_ACTION", "NONE"}


class CS2RIG_OT_browse_clips(bpy.types.Operator):
    bl_idname = "cs2rig.browse_clips"
    bl_label = "Browse Clips"
    bl_description = "Find a clip by category or name, see its skeleton play, and click to load it on the selected agent"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        close_browser()
        try:
            current = session.get()
            data = current.catalog()
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        clips = [cb.Clip(path) for path in data.get("clips", [])]
        if not clips:
            self.report({"ERROR"}, "No clips found. Press Refresh first.")
            return {"CANCELLED"}
        wm = context.window_manager
        armature = importer.find_agent_armature(context)
        key = importer.held_weapon_key(armature) if armature else ""
        weapon = next((w for w in catalog_mod.catalog_weapons(data) if w.key == key), None) if key else None
        status_path = _cache_file(current.cache, "clip_status_file", "clip_status.json")
        browser = Browser(
            clips=clips,
            status=cb.read_status(status_path),
            preview_dir=_cache_file(current.cache, "clip_preview_dir", "clip_previews"),
            family=catalog_mod.clip_family(weapon) if weapon else "",
            weapon=weapon.key if weapon else "",
            agent=armature.name.removesuffix(".skeleton") if armature else "",
            open_family=bool(weapon) and wm.cs2rig_match_clips,
            can_build=_can_build_previews(),
        )
        current_clip = catalog_mod.CLIP_PREFIX + wm.cs2rig_clip if wm.cs2rig_clip else ""
        browser.show(next((clip for clip in browser.visible if clip.path == current_clip), None))
        _open.update(
            browser=browser,
            area=context.area.as_pointer(),
            window=context.window,
            handler=bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), "WINDOW", "POST_PIXEL"),
            timer=wm.event_timer_add(1.0 / FRAME_RATE, window=context.window),
        )
        self._region = _window_region(context.area)
        wm.modal_handler_add(self)
        context.area.tag_redraw()
        log_mod.logger.info("clip browser opened, %d clips, status %s", len(clips), "read" if browser.status is not None else "missing")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        browser = _open.get("browser")
        region = self._region
        if browser is None or region is None or context.area is None:
            close_browser()
            return {"CANCELLED"}
        try:
            region.width
        except ReferenceError:
            close_browser()
            return {"CANCELLED"}
        if event.type in _PASS:
            if event.type == "TIMER" and browser.shown is not None:
                context.area.tag_redraw()  # plays the preview
            return {"PASS_THROUGH"}
        lay = cb.layout(region.width, region.height, _ui_scale(context), _covered(context.area, region))
        x, y = event.mouse_x - region.x, event.mouse_y - region.y
        target = cb.hit(lay, x, y, len(browser.tree), browser.tree_scroll, len(browser.visible), browser.list_scroll,
                        button=browser.can_build and not browser.has_previews and browser.shown is not None)
        context.area.tag_redraw()
        return self._handle(context, event, browser, lay, target)

    def _handle(self, context, event, browser: Browser, lay: cb.Layout, target: tuple[str, int]):
        kind, index = target
        browser.hover = target
        if browser.dragging is not None:
            return self._drag(event, browser, lay)
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if kind == "list":
                browser.focus = index
                browser.show(browser.visible[index])
            return {"RUNNING_MODAL"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN"}:
            step = 3 * lay.row * (-1 if event.type == "WHEELUPMOUSE" else 1)
            if event.type == "TRACKPADPAN":
                step = event.mouse_prev_y - event.mouse_y
            if kind == "tree":
                browser.tree_scroll = cb.clamp_scroll(browser.tree_scroll + step, len(browser.tree), lay.row, lay.tree)
            else:
                browser.list_scroll = cb.clamp_scroll(browser.list_scroll + step, len(browser.visible), lay.row, lay.list)
            return {"RUNNING_MODAL"}
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            close_browser()
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE":
            if kind in {"close", "outside"}:
                close_browser()
                return {"CANCELLED"}
            if kind in {"tree-bar", "list-bar"}:
                name = kind.removesuffix("-bar")
                view, rows, scroll = self._column(browser, lay, name)
                bar = cb.thumb(view, lay.row, scroll, rows, lay.scale)
                y = event.mouse_y - self._region.y
                # Grab the thumb where it was clicked; a click on the track jumps the thumb's middle there.
                grab = bar.top - y if bar.y <= y <= bar.top else bar.h / 2
                browser.dragging = (name, grab)
                return self._drag(event, browser, lay)
            if kind == "tree":
                browser.node = index
                browser.list_scroll = 0.0
                browser.focus = -1
                browser.refilter()
            elif kind == "list":
                return self._load(context, browser, browser.visible[index])
            elif kind == "button":
                if not bpy.ops.cs2rig.build_clip_previews.poll():
                    browser.message = "Press Refresh first, or wait for the previews being built to finish."
                    return {"RUNNING_MODAL"}
                close_browser()
                try:
                    bpy.ops.cs2rig.build_clip_previews("INVOKE_DEFAULT")
                except RuntimeError:
                    pass  # the operator has reported why
                return {"FINISHED"}
            return {"RUNNING_MODAL"}
        if event.type in {"UP_ARROW", "DOWN_ARROW"} and browser.visible:
            step = -1 if event.type == "UP_ARROW" else 1
            browser.focus = min(max(0, browser.focus + step), len(browser.visible) - 1)
            browser.list_scroll = cb.scroll_to_show(browser.list_scroll, browser.focus, lay.row, lay.list)
            browser.show(browser.visible[browser.focus])
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"} and 0 <= browser.focus < len(browser.visible):
            return self._load(context, browser, browser.visible[browser.focus])
        if event.type == "BACK_SPACE":
            browser.query = "" if event.ctrl else browser.query[:-1]
            self._searched(browser)
            return {"RUNNING_MODAL"}
        text = event.unicode
        if text and text.isprintable() and not (event.ctrl or event.alt or event.oskey):
            browser.query += text
            self._searched(browser)
        return {"RUNNING_MODAL"}

    @staticmethod
    def _column(browser: Browser, lay: cb.Layout, name: str) -> tuple[cb.Rect, int, float]:
        if name == "tree":
            return lay.tree, len(browser.tree), browser.tree_scroll
        return lay.list, len(browser.visible), browser.list_scroll

    def _drag(self, event, browser: Browser, lay: cb.Layout):
        """Follow the mouse with a held scrollbar until the button is let go."""
        name, grab = browser.dragging
        view, rows, _scroll = self._column(browser, lay, name)
        y = event.mouse_y - self._region.y
        scroll = cb.scroll_for_thumb(view, lay.row, rows, lay.scale, y + grab)
        if name == "tree":
            browser.tree_scroll = scroll
        else:
            browser.list_scroll = scroll
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" or event.type in {"ESC", "RIGHTMOUSE"}:
            browser.dragging = None
        return {"RUNNING_MODAL"}

    @staticmethod
    def _searched(browser: Browser) -> None:
        browser.list_scroll = 0.0
        browser.focus = 0
        browser.message = ""
        browser.refilter()
        if browser.visible:
            browser.show(browser.visible[0])

    def _load(self, context, browser: Browser, clip: cb.Clip):
        if not clip.usable:
            browser.message = f"{clip.name} cannot be loaded: {clip.reason[:1].lower()}{clip.reason[1:]}."
            return {"RUNNING_MODAL"}
        if not bpy.ops.cs2rig.load_clip.poll():
            browser.message = "Select an imported agent first, then click a clip to load it."
            return {"RUNNING_MODAL"}
        close_browser()
        context.window_manager.cs2rig_clip = clip.label
        try:
            bpy.ops.cs2rig.load_clip()
        except RuntimeError:
            pass  # the operator has reported why
        return {"FINISHED"}

    def cancel(self, context):
        close_browser()


def register():
    bpy.utils.register_class(CS2RIG_OT_browse_clips)
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    close_browser()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    bpy.utils.unregister_class(CS2RIG_OT_browse_clips)
