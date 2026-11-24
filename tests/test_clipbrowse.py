import json

import pytest

from core import clipbrowse as cb

PATHS = [
    "animation/anims/world/rifle/_default_rifle/run_n_rifle",
    "animation/anims/world/rifle/rifle_ak/reload_ak",
    "animation/anims/world/rifle/rifle_aug/reload_aug",
    "animation/anims/world/knife/_default_knife/idle_knife",
    "animation/anims/world/shared/jump_additive",
    "animation/anims/ui_anims/main_menu/ct/ct_main_menu_elite_shrug",
    "animation/anims/ui_anims/buy_menu/ct/ct_buymenu_ak",
    "animation/anims/viewmodel/knife/draw",
    "animation/anims/chicken/world/walk",
    "animation/anims/egg/chick_egg_reaction01",
    "animation/anims/oddity/thing/clip",
]
CLIPS = [cb.Clip(path) for path in PATHS]


def by_name(name):
    return next(clip for clip in CLIPS if clip.name == name)


def test_clip_parts():
    clip = by_name("reload_ak")
    assert clip.label == "world/rifle/rifle_ak/reload_ak"
    assert (clip.group, clip.category, clip.folder) == ("world", "world/rifle", "rifle_ak")
    assert clip.usable and not clip.reason
    egg = by_name("chick_egg_reaction01")
    assert (egg.category, egg.folder) == ("egg", "")
    assert not egg.usable and "egg" in egg.reason
    assert by_name("ct_main_menu_elite_shrug").usable


def test_tree_order_and_dimmed_groups():
    tree = cb.build_tree(CLIPS)
    keys = [node.key for node in tree]
    assert keys == [
        "all", "world", "world/rifle", "world/knife", "world/shared",
        "ui_anims", "ui_anims/buy_menu", "ui_anims/main_menu",
        "not_for_agents", "viewmodel", "chicken", "egg", "other",
    ]
    assert [node.label for node in tree if node.key.startswith("ui_anims/")] == ["Buy menu", "Main menu"]
    assert all(node.dimmed and node.reason for node in tree[keys.index("not_for_agents"):])
    assert not any(node.dimmed for node in tree[:keys.index("not_for_agents")])


def test_counts_and_search():
    tree = cb.build_tree(CLIPS)
    keys = [node.key for node in tree]
    counts = dict(zip(keys, cb.counts(CLIPS, tree, "")))
    assert counts["all"] == 7 and counts["world"] == 5 and counts["world/rifle"] == 3
    assert counts["not_for_agents"] == 4 and counts["viewmodel"] == 1 and counts["other"] == 1
    found = dict(zip(keys, cb.counts(CLIPS, tree, "AK  reload")))
    assert found["world/rifle"] == 1 and found["ui_anims"] == 0
    assert cb.matches(by_name("ct_buymenu_ak"), "buy ak")
    assert not cb.matches(by_name("ct_buymenu_ak"), "buy aug")


def test_list_puts_the_held_weapon_first_and_world_before_menus():
    tree = cb.build_tree(CLIPS)
    rifle = tree[[node.key for node in tree].index("world/rifle")]
    folder = cb.held_folder(CLIPS, "rifle", "ak47")
    assert folder == "rifle_ak"
    assert [clip.name for clip in cb.clips_in(CLIPS, rifle, "", folder)] == ["reload_ak", "run_n_rifle", "reload_aug"]
    everything = [clip.group for clip in cb.clips_in(CLIPS, tree[0], "")]
    assert everything == ["world"] * 5 + ["ui_anims"] * 2
    assert [clip.category for clip in cb.clips_in(CLIPS, tree[0], "")][:4] == ["world/rifle"] * 3 + ["world/knife"]


def test_held_folder_matching():
    assert cb.weapon_folder_score(by_name("reload_aug"), "aug") == 3
    assert cb.weapon_folder_score(by_name("reload_aug"), "ak47") == 0
    assert cb.weapon_folder_score(by_name("run_n_rifle"), "rifle") == 0  # '_default_rifle' names no weapon
    assert cb.held_folder(CLIPS, "knife", "knife_karambit") == ""
    knives = [cb.Clip(f"animation/anims/world/knife/{folder}/idle") for folder in ("_default_knife", "default_ct", "knife_karambit", "knife_m9")]
    assert cb.held_folder(knives, "knife", "knife_karambit") == "knife_karambit"
    assert cb.held_folder(knives, "knife", "knife_default_ct") == "default_ct"
    assert cb.held_folder(knives, "knife", "knife_default_t") == ""
    rifles = [cb.Clip(f"animation/anims/world/rifle/{folder}/idle") for folder in ("rifle_m4a1_silencer", "rifle_m4a4", "rifle_scar")]
    assert cb.held_folder(rifles, "rifle", "m4a4") == "rifle_m4a4"
    assert cb.held_folder(rifles, "rifle", "m4a1_silencer") == "rifle_m4a1_silencer"
    assert cb.held_folder(rifles, "rifle", "scar20") == "rifle_scar"


def test_default_node():
    tree = cb.build_tree(CLIPS)
    assert tree[cb.default_node(tree, "rifle")].key == "world/rifle"
    assert cb.default_node(tree, "") == 0
    assert cb.default_node(tree, "grenade") == 0  # no grenade clips in this catalog


def test_status_and_badges(tmp_path):
    path = tmp_path / "clip_status.json"
    assert cb.read_status(path) is None
    path.write_text(json.dumps({"version": 1, "clips": {PATHS[1]: {"verdict": "collapsed", "note": "folds"}, PATHS[0]: {}}}))
    status = cb.read_status(path)
    assert cb.verdict(status, CLIPS[1]) == "collapsed"
    assert cb.verdict(status, CLIPS[0]) == "unchecked"
    assert cb.verdict(status, CLIPS[2]) == "unchecked"
    assert cb.verdict(None, CLIPS[2]) == ""
    assert cb.badge("other-skeleton") == "OTHER SKELETON"
    assert cb.badge("strange-new-thing") == "STRANGE NEW THING"
    path.write_text("not json")
    assert cb.read_status(path) is None


def _preview_data(frames):
    return {
        "version": 1,
        "clip": PATHS[0],
        "fps": 10,
        "bones": ["pelvis", "spine", "foot_L"],
        "parents": [-1, 0, 0],
        "side": ["C", "C", "L"],
        "frames": frames,
    }


def test_preview_reading_and_blending(tmp_path):
    path = cb.preview_file(tmp_path, PATHS[0])
    assert path.parent == tmp_path and len(path.stem) == 16 and path.suffix == ".json"
    assert cb.read_preview(path) is None
    frames = [[0, 0, 1, 0, 0, 1.5, 0.2, 0, 0], [1, 0, 1, 1, 0, 1.5, 1.2, 0, 0], [2, 0, 1, 2, 0, 1.5, 2.2, 0, 0]]
    path.write_text(json.dumps(_preview_data(frames)))
    preview = cb.read_preview(path)
    assert preview.duration == pytest.approx(0.2)
    assert preview.root == 0
    assert preview.pose(0.05)[0] == pytest.approx((0.5, 0, 1))
    assert preview.pose(0.25)[0] == pytest.approx((0.5, 0, 1))  # loops
    bad = _preview_data([[0, 0, 1]])
    path.write_text(json.dumps(bad))
    assert cb.read_preview(path) is None
    bad = _preview_data(frames)
    bad["parents"] = [-1, 0, 7]
    path.write_text(json.dumps(bad))
    assert cb.read_preview(path) is None


def test_projection_keeps_the_figure_in_the_stage_and_follows_the_root():
    frames = [[x, 0, 1, x, 0, 1.6, x + 0.2, 0, 0] for x in (0.0, 2.0, 4.0)]
    preview = cb.Preview(PATHS[0], 10, ["pelvis", "spine", "foot_L"], [-1, 0, 0], ["C", "C", "L"], frames)
    stage = cb.Rect(100, 50, 300, 400)
    first, _, floor, travel = cb.project(preview, preview.pose(0.0), stage)
    last, depths, _, travel_last = cb.project(preview, preview.pose(0.15), stage)
    flat = lambda points: [value for point in points for value in point]  # noqa: E731
    assert flat(first) == pytest.approx(flat(last))  # the root stays in the middle while the agent moves
    assert travel == 0 and travel_last > 0
    assert len(depths) == 3
    for x, y in first:
        assert stage.contains(x, y)
    assert stage.y <= floor < first[0][1]


def test_layout_fits_and_scales():
    for width, height, scale in ((1600, 900, 1.0), (3200, 1800, 2.0), (1000, 620, 1.0), (700, 400, 1.0)):
        lay = cb.layout(width, height, scale)
        assert 0 <= lay.panel.x and lay.panel.right <= width and 0 <= lay.panel.y and lay.panel.top <= height
        for part in (lay.tree, lay.search, lay.list, lay.preview, lay.header, lay.footer):
            assert lay.panel.x <= part.x and part.right <= lay.panel.right + 0.5
            assert lay.panel.y <= part.y and part.top <= lay.panel.top + 0.5
        assert lay.tree.right <= lay.list.x and lay.list.right <= lay.preview.x
    small, big = cb.layout(1600, 900, 1.0), cb.layout(3200, 1800, 2.0)
    assert big.row == 2 * small.row and big.text == 2 * small.text
    assert big.panel.w == pytest.approx(2 * small.panel.w, abs=2)


def test_hit_testing_and_scrolling():
    lay = cb.layout(1600, 900, 1.0)
    row = lay.row
    below_top = lambda view, n: view.top - row * n - row / 2  # noqa: E731
    assert cb.hit(lay, 5, 5, 10, 0, 50, 0) == ("outside", -1)
    assert cb.hit(lay, lay.close.x + 2, lay.close.y + 2, 10, 0, 50, 0) == ("close", -1)
    assert cb.hit(lay, lay.tree.x + 5, below_top(lay.tree, 2), 10, 0, 50, 0) == ("tree", 2)
    assert cb.hit(lay, lay.list.x + 5, below_top(lay.list, 0), 10, 0, 50, 0) == ("list", 0)
    assert cb.hit(lay, lay.list.x + 5, below_top(lay.list, 0), 10, 0, 50, row * 4) == ("list", 4)
    assert cb.hit(lay, lay.list.x + 5, below_top(lay.list, 3), 10, 0, 2, 0) == ("panel", -1)
    assert cb.hit(lay, lay.search.x + 5, lay.search.y + 5, 10, 0, 50, 0) == ("search", -1)
    assert cb.hit(lay, lay.button.x + 2, lay.button.y + 2, 10, 0, 50, 0) == ("panel", -1)
    assert cb.hit(lay, lay.button.x + 2, lay.button.y + 2, 10, 0, 50, 0, button=True) == ("button", -1)
    # Scrolling stops at the ends; rows that fit do not scroll at all.
    assert cb.clamp_scroll(-50, 100, row, lay.list) == 0
    assert cb.clamp_scroll(1e9, 100, row, lay.list) == pytest.approx(100 * row - lay.list.h)
    assert cb.clamp_scroll(80, 3, row, lay.list) == 0
    # The keyboard scrolls just far enough to show its row.
    shown = cb.scroll_to_show(0, 60, row, lay.list)
    assert cb.row_at(lay.list, row, shown, 100, lay.list.y + 1) == 60
    assert cb.scroll_to_show(shown, 2, row, lay.list) == 2 * row
    rect = cb.row_rect(lay.list, row, shown, 60)
    assert cb.row_at(lay.list, row, shown, 100, rect.y + rect.h / 2) == 60


def test_layout_stays_clear_of_overlapping_sidebars():
    lay = cb.layout(1600, 900, 1.0, covered=(40, 0, 320, 30))
    assert lay.panel.x >= 40 and lay.panel.right <= 1600 - 320 and lay.panel.top <= 900 - 30
    narrow = cb.layout(800, 600, 1.0, covered=(40, 0, 320, 0))
    assert narrow.panel.right > 800 - 320  # too narrow to dodge them: the whole region is used
