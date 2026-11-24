"""What the clip browser shows: the category tree, the filtered list, its layout and skeleton previews.

No Blender imports here; cs2_rigs/clip_browser.py draws what this module works out.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import CLIP_PREFIX

# World clip folders in the order the tree lists them.
WORLD_FAMILIES = ("rifle", "knife", "pistol", "shared", "equipment", "grenade")
MENU_GROUP = "ui_anims"
# Top folders whose clips target another skeleton, with the reason shown for them.
NOT_FOR_AGENTS = {
    "viewmodel": ("First-person arms", "Made for the first-person arms, not an agent"),
    "chicken": ("Chicken", "Made for the chicken's skeleton"),
    "egg": ("Egg", "Made for the chicken egg"),
}
OTHER_REASON = "Not a third-person agent clip"

ALL_NODE = "all"
UNUSABLE_NODE = "not_for_agents"

# clip_status.json verdicts -> badge text. Anything else shows as written.
BADGES = {
    "ok": "OK",
    "additive": "ADDITIVE",
    "no-animation": "NO MOTION",
    "collapsed": "COLLAPSED",
    "length-mismatch": "LENGTH",
    "other-skeleton": "OTHER SKELETON",
    "converter-error": "ERROR",
    "timeout": "TIMEOUT",
    "unchecked": "UNCHECKED",
}


def folder_title(folder: str) -> str:
    """'main_menu' -> 'Main menu'."""
    text = folder.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class Clip:
    path: str  # "animation/anims/world/rifle/rifle_ak/reload_ak"

    @property
    def label(self) -> str:
        """The path after animation/anims/, which the Load Clip operator takes."""
        return self.path[len(CLIP_PREFIX):] if self.path.startswith(CLIP_PREFIX) else self.path

    @property
    def parts(self) -> list[str]:
        return self.label.split("/")

    @property
    def group(self) -> str:
        return self.parts[0]

    @property
    def category(self) -> str:
        """'world/rifle', 'ui_anims/main_menu'; a top folder alone when the clip sits directly in it."""
        parts = self.parts
        return "/".join(parts[:2]) if len(parts) > 2 else parts[0]

    @property
    def folder(self) -> str:
        """The folders between the category and the clip, e.g. 'rifle_ak'."""
        return "/".join(self.parts[2:-1])

    @property
    def name(self) -> str:
        return self.parts[-1]

    @property
    def usable(self) -> bool:
        return self.group == "world" or self.group == MENU_GROUP

    @property
    def reason(self) -> str:
        """Why this clip cannot go on an agent; empty when it can."""
        if self.usable:
            return ""
        return NOT_FOR_AGENTS.get(self.group, ("", OTHER_REASON))[1]


@dataclass(frozen=True)
class TreeNode:
    key: str
    label: str
    depth: int
    dimmed: bool = False
    reason: str = ""

    def holds(self, clip: Clip) -> bool:
        if self.key == ALL_NODE:
            return clip.usable
        if self.key == UNUSABLE_NODE:
            return not clip.usable
        return clip.group == self.key or clip.category == self.key or (self.key == "other" and _is_other(clip))


def _is_other(clip: Clip) -> bool:
    return not clip.usable and clip.group not in NOT_FOR_AGENTS


def build_tree(clips: list[Clip]) -> list[TreeNode]:
    """World families first, then menu poses, then the dimmed groups no agent can play."""
    groups = {clip.group for clip in clips}
    categories = {clip.category for clip in clips}
    nodes = [TreeNode(ALL_NODE, "All clips", 0)]
    if "world" in groups:
        nodes.append(TreeNode("world", "World", 0))
        families = [f for f in WORLD_FAMILIES if f"world/{f}" in categories]
        families += sorted(c.split("/")[1] for c in categories if c.startswith("world/") and c.split("/")[1] not in WORLD_FAMILIES)
        nodes += [TreeNode(f"world/{family}", folder_title(family), 1) for family in families]
    if MENU_GROUP in groups:
        nodes.append(TreeNode(MENU_GROUP, "Menu poses", 0))
        menus = sorted(c for c in categories if c.startswith(MENU_GROUP + "/"))
        nodes += [TreeNode(menu, folder_title(menu.split("/")[1]), 1) for menu in menus]
    unusable = [g for g in NOT_FOR_AGENTS if g in groups]
    has_other = any(_is_other(clip) for clip in clips)
    if unusable or has_other:
        nodes.append(TreeNode(UNUSABLE_NODE, "Not for agents", 0, dimmed=True, reason="These clips target another skeleton"))
        for group in unusable:
            label, reason = NOT_FOR_AGENTS[group]
            nodes.append(TreeNode(group, label, 1, dimmed=True, reason=reason))
        if has_other:
            nodes.append(TreeNode("other", "Other", 1, dimmed=True, reason=OTHER_REASON))
    return nodes


def matches(clip: Clip, query: str) -> bool:
    """Every word of the search appears somewhere in the clip's path."""
    text = clip.label.lower()
    return all(word in text for word in query.lower().split())


def _names(name: str) -> set[str]:
    """'rifle_ak' -> {'rifle_ak', 'ak'}: a name with and without its first word."""
    return {name, name.split("_", 1)[1]} if "_" in name else {name}


def weapon_folder_score(clip: Clip, weapon_key: str) -> int:
    """How well the clip's folder names the held weapon, 0 for not at all.

    Folders name weapons loosely: 'rifle_ak' holds the ak47's clips, 'knife_karambit' the
    karambit's and 'default_ct' those of knife_default_ct. '_default_*' folders are shared.
    """
    best = 0
    keys = _names(weapon_key) if weapon_key else set()
    for part in clip.folder.split("/"):
        if part.startswith("_"):
            continue
        for stem in _names(part):
            if len(stem) >= 2 and any(key == stem or key.startswith(stem) for key in keys):
                best = max(best, len(stem))
    return best


def held_folder(clips: list[Clip], family: str, weapon_key: str) -> str:
    """The folder of clips made for the held weapon, e.g. 'rifle_ak'; empty when it has none."""
    best, best_score = "", 0
    for clip in clips:
        if clip.category != f"world/{family}":
            continue
        score = weapon_folder_score(clip, weapon_key)
        if score > best_score:
            best, best_score = clip.folder, score
    return best


def _order(clip: Clip) -> tuple:
    """Clips in the order the tree lists their categories."""
    if clip.group == "world":
        family = clip.parts[1] if len(clip.parts) > 2 else ""
        rank = WORLD_FAMILIES.index(family) if family in WORLD_FAMILIES else len(WORLD_FAMILIES)
        return (0, rank)
    if clip.group == MENU_GROUP:
        return (1, 0)
    groups = list(NOT_FOR_AGENTS)
    return (2, groups.index(clip.group) if clip.group in groups else len(groups))


def clips_in(clips: list[Clip], node: TreeNode, query: str, first_folder: str = "") -> list[Clip]:
    """The clips under a tree node that match the search, the held weapon's own folder first."""
    found = [clip for clip in clips if node.holds(clip) and matches(clip, query)]
    return sorted(found, key=lambda clip: (not first_folder or clip.folder != first_folder, _order(clip), clip.label))


def counts(clips: list[Clip], tree: list[TreeNode], query: str) -> list[int]:
    """How many matching clips each tree node holds."""
    found = [clip for clip in clips if matches(clip, query)]
    return [sum(1 for clip in found if node.holds(clip)) for node in tree]


def default_node(tree: list[TreeNode], family: str) -> int:
    """Open on the held weapon's family when the panel filters by it, otherwise on every clip."""
    keys = [node.key for node in tree]
    if family and f"world/{family}" in keys:
        return keys.index(f"world/{family}")
    return 0


# Status and previews (see the clip sweep's clip_status.json and clip_previews/).


def read_status(path: Path) -> dict[str, dict] | None:
    """Clip path -> its record from clip_status.json; None when the clips have not been checked."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    clips = data.get("clips") if isinstance(data, dict) else None
    return clips if isinstance(clips, dict) else None


def verdict(status: dict[str, dict] | None, clip: Clip) -> str:
    """The clip's verdict; '' when there is no status file at all."""
    if status is None:
        return ""
    record = status.get(clip.path)
    return str(record.get("verdict") or "unchecked") if isinstance(record, dict) else "unchecked"


def badge(verdict_name: str) -> str:
    return BADGES.get(verdict_name, verdict_name.replace("-", " ").upper())


def preview_file(preview_dir: Path, clip_path: str) -> Path:
    return Path(preview_dir) / f"{hashlib.sha1(clip_path.encode('utf-8')).hexdigest()[:16]}.json"


@dataclass
class Preview:
    clip: str
    fps: float
    bones: list[str]
    parents: list[int]
    side: list[str]
    frames: list[list[float]]
    _framing: tuple | None = field(default=None, repr=False)

    @property
    def root(self) -> int:
        """The bone that carries root motion: the first one without a parent."""
        return self.parents.index(-1) if -1 in self.parents else 0

    @property
    def duration(self) -> float:
        return (len(self.frames) - 1) / self.fps if len(self.frames) > 1 else 0.0

    def pose(self, seconds: float) -> list[tuple[float, float, float]]:
        """Bone positions at a time in the loop, blended between the sampled frames."""
        count = len(self.bones)
        if len(self.frames) == 1 or self.duration <= 0:
            row = self.frames[0]
            return [tuple(row[i * 3:i * 3 + 3]) for i in range(count)]
        at = (seconds % self.duration) * self.fps
        index = min(int(at), len(self.frames) - 2)
        blend = at - index
        a, b = self.frames[index], self.frames[index + 1]
        return [tuple(a[j] + (b[j] - a[j]) * blend for j in range(i * 3, i * 3 + 3)) for i in range(count)]


def read_preview(path: Path) -> Preview | None:
    """A skeleton preview, or None when it is missing or does not hold together."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        bones, parents, frames = data["bones"], data["parents"], data["frames"]
        side = data.get("side") or ["C"] * len(bones)
        fps = float(data.get("fps") or 15)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    count = len(bones)
    if not count or len(parents) != count or len(side) != count or not frames or fps <= 0:
        return None
    if any(len(row) != count * 3 for row in frames) or any(not -1 <= p < count for p in parents):
        return None
    return Preview(str(data.get("clip", "")), fps, list(bones), list(parents), list(side), frames)


# Layout. Coordinates are pixels from the region's bottom-left corner, as Blender gives them.


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def right(self) -> float:
        return self.x + self.w

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.top

    def inset(self, dx: float, dy: float | None = None) -> Rect:
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, max(0.0, self.w - 2 * dx), max(0.0, self.h - 2 * dy))


@dataclass(frozen=True)
class Layout:
    scale: float
    panel: Rect
    header: Rect
    close: Rect
    tree: Rect
    search: Rect
    list: Rect
    preview: Rect
    stage: Rect  # where the skeleton is drawn inside the preview
    button: Rect  # Build Clip Previews, inside the preview
    footer: Rect
    row: float  # height of a list or tree row
    pad: float
    text: float  # font sizes in pixels
    small: float
    title: float


def layout(width: float, height: float, scale: float, covered: tuple[float, float, float, float] = (0, 0, 0, 0)) -> Layout:
    """Place the browser in a region, sized for Blender's UI scale and shrunk to fit small regions.

    `covered` is how much of the region's left, bottom, right and top edges other regions draw
    over (the toolbar, sidebar and header when Region Overlap is on); the browser stays clear of it.
    """
    s = max(0.5, scale)
    pad = round(10 * s)
    margin = round(24 * s)
    left, bottom, right, top = covered
    if width - left - right < 600 * s:  # too narrow to dodge the sidebars; use the whole region
        left = right = 0
    room_w, room_h = width - left - right, height - bottom - top
    panel_w = max(0, min(room_w - 2 * margin, round(1180 * s)))
    panel_h = max(0, min(room_h - 2 * margin, round(660 * s)))
    panel = Rect(round(left + (room_w - panel_w) / 2), round(bottom + (room_h - panel_h) / 2), panel_w, panel_h)
    header_h = round(34 * s)
    footer_h = round(26 * s)
    header = Rect(panel.x, panel.top - header_h, panel.w, header_h)
    footer = Rect(panel.x, panel.y, panel.w, footer_h)
    close = Rect(header.right - header_h, header.y, header_h, header_h)
    body = Rect(panel.x + pad, footer.top, panel.w - 2 * pad, header.y - footer.top - pad)
    # Columns: tree, list, preview. The preview gives way first on narrow regions.
    tree_w = min(round(210 * s), body.w * 0.24)
    preview_w = min(round(380 * s), body.w * 0.36)
    list_w = max(0.0, body.w - tree_w - preview_w - 2 * pad)
    tree = Rect(body.x, body.y, tree_w, body.h)
    search_h = round(26 * s)
    column = Rect(tree.right + pad, body.y, list_w, body.h)
    search = Rect(column.x, column.top - search_h, column.w, search_h)
    clip_list = Rect(column.x, body.y, column.w, column.h - search_h - round(6 * s))
    preview = Rect(column.right + pad, body.y, preview_w, body.h)
    head = round(54 * s)
    foot = round(56 * s)
    stage = Rect(preview.x + pad, preview.y + foot, preview.w - 2 * pad, max(0, preview.h - head - foot))
    button_w = min(stage.w, round(190 * s))
    button = Rect(stage.x + (stage.w - button_w) / 2, stage.y + stage.h / 2 - round(40 * s), button_w, round(26 * s))
    return Layout(
        scale=s,
        panel=panel,
        header=header,
        close=close,
        tree=tree,
        search=search,
        list=clip_list,
        preview=preview,
        stage=stage,
        button=button,
        footer=footer,
        row=round(22 * s),
        pad=pad,
        text=round(12 * s),
        small=round(10 * s),
        title=round(14 * s),
    )


def max_scroll(rows: int, row: float, view: Rect) -> float:
    return max(0.0, rows * row - view.h)


def clamp_scroll(scroll: float, rows: int, row: float, view: Rect) -> float:
    return min(max(0.0, scroll), max_scroll(rows, row, view))


def row_at(view: Rect, row: float, scroll: float, rows: int, py: float) -> int:
    """The row under a height in a scrolled column, or -1."""
    index = int((view.top - py + scroll) // row)
    return index if 0 <= index < rows else -1


def row_rect(view: Rect, row: float, scroll: float, index: int) -> Rect:
    return Rect(view.x, view.top - (index + 1) * row + scroll, view.w, row)


def scroll_to_show(scroll: float, index: int, row: float, view: Rect) -> float:
    """Scroll just enough to show a row moved to with the keyboard."""
    top, bottom = index * row, (index + 1) * row
    if top < scroll:
        return top
    if bottom > scroll + view.h:
        return bottom - view.h
    return scroll


def hit(lay: Layout, px: float, py: float, tree_rows: int, tree_scroll: float, list_rows: int, list_scroll: float,
        button: bool = False) -> tuple[str, int]:
    """What is under the mouse: ('tree', row), ('list', row), ('close'|'search'|'button'|'panel'|'outside', -1)."""
    if not lay.panel.contains(px, py):
        return ("outside", -1)
    if lay.close.contains(px, py):
        return ("close", -1)
    if lay.tree.contains(px, py):
        index = row_at(lay.tree, lay.row, tree_scroll, tree_rows, py)
        return ("tree", index) if index >= 0 else ("panel", -1)
    if lay.search.contains(px, py):
        return ("search", -1)
    if lay.list.contains(px, py):
        index = row_at(lay.list, lay.row, list_scroll, list_rows, py)
        return ("list", index) if index >= 0 else ("panel", -1)
    if button and lay.button.contains(px, py):
        return ("button", -1)
    return ("panel", -1)


# The skeleton drawing: a fixed three-quarter view that follows the root across the floor.

VIEW_YAW = math.radians(30.0)


def _view(x: float, y: float, z: float) -> tuple[float, float]:
    """Screen x and height for a point, seen from the agent's front-left (the agent faces -Y)."""
    cos, sin = math.cos(VIEW_YAW), math.sin(VIEW_YAW)
    return (x * cos - y * sin, z)


def _depth(x: float, y: float) -> float:
    """Distance into the screen; larger is farther from the viewer."""
    return x * math.sin(VIEW_YAW) + y * math.cos(VIEW_YAW)


def framing(preview: Preview) -> tuple[float, float, float, float]:
    """Bounds of every frame, root motion taken out: (left, right, floor, top) in metres."""
    if preview._framing is None:
        left = right = floor = top = None
        count, root = len(preview.bones), preview.root * 3
        for row in preview.frames:
            rx, ry = row[root], row[root + 1]
            for i in range(count):
                sx, sz = _view(row[i * 3] - rx, row[i * 3 + 1] - ry, row[i * 3 + 2])
                left = sx if left is None else min(left, sx)
                right = sx if right is None else max(right, sx)
                floor = sz if floor is None else min(floor, sz)
                top = sz if top is None else max(top, sz)
        # Joints are bone heads; leave room above for the head and below for the feet.
        preview._framing = (left, right, min(floor, 0.0), max(top + 0.2, 1.0))
    return preview._framing


def project(preview: Preview, pose: list[tuple[float, float, float]], stage: Rect) -> tuple[list[tuple[float, float]], list[float], float, float]:
    """Screen points for a pose, their depths, the floor height and how far the root has travelled in screen x."""
    left, right, floor, top = framing(preview)
    width, height = max(right - left, 0.3), max(top - floor, 0.3)
    scale = min(stage.w * 0.8 / width, stage.h * 0.82 / height)
    rx, ry = pose[preview.root][0], pose[preview.root][1]
    centre_x = stage.x + stage.w / 2 - (left + right) / 2 * scale
    base = stage.y + stage.h * 0.08 - floor * scale
    points = []
    for x, y, z in pose:
        sx, sz = _view(x - rx, y - ry, z)
        points.append((centre_x + sx * scale, base + sz * scale))
    start_x, start_y = preview.frames[0][preview.root * 3], preview.frames[0][preview.root * 3 + 1]
    travel = _view(rx - start_x, ry - start_y, 0.0)[0] * scale
    depths = [_depth(x, y) for x, y, _z in pose]
    return points, depths, base + floor * scale, travel
