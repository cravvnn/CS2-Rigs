"""Unit tests for core first-person viewmodel math, presets, clip filtering, and targets."""

import math

import pytest

from core import clipbrowse as cb
from core import clipdata, targets, viewmodel
from core.catalog import Weapon


class FakeObj(dict):
    """Minimal stand-in for bpy.types.Object."""

    def __init__(self, name: str, obj_type: str = "ARMATURE", parent=None, **props):
        super().__init__(props)
        self.name = name
        self.type = obj_type
        self.parent = parent
        self._children: list[FakeObj] = []
        if parent is not None:
            parent._children.append(self)

    @property
    def children_recursive(self) -> list["FakeObj"]:
        out = []
        for child in self._children:
            out.append(child)
            out.extend(child.children_recursive)
        return out


# --- FOV & Offset Math ------------------------------------------------------


def test_source2_hor_plus_fov_math():
    # Classic 68 deg 4:3 -> 53.668 deg vertical FOV -> 83.933 deg 16:9 horizontal FOV
    vfov_68 = viewmodel.source2_fov_to_vertical_rad(68.0)
    assert math.degrees(vfov_68) == pytest.approx(53.668, abs=1e-2)
    assert viewmodel.horizontal_fov_deg(vfov_68, 16.0 / 9.0) == pytest.approx(83.933, abs=1e-2)
    assert viewmodel.vertical_rad_to_source2_fov(vfov_68) == pytest.approx(68.0, abs=1e-6)

    # Desktop 60 deg 4:3 -> 46.826 deg vertical FOV -> 75.178 deg 16:9 horizontal FOV
    vfov_60 = viewmodel.source2_fov_to_vertical_rad(60.0)
    assert math.degrees(vfov_60) == pytest.approx(46.826, abs=1e-2)
    assert viewmodel.horizontal_fov_deg(vfov_60, 16.0 / 9.0) == pytest.approx(75.178, abs=1e-2)
    assert viewmodel.vertical_rad_to_source2_fov(vfov_60) == pytest.approx(60.0, abs=1e-6)

    # 4:3 aspect round-trips back to the input 4:3 FOV
    assert viewmodel.horizontal_fov_deg(vfov_68, 4.0 / 3.0) == pytest.approx(68.0, abs=1e-6)


def test_presets_and_offset_inches_conversion():
    classic = viewmodel.get_preset("classic")
    assert classic.fov == 68.0
    assert classic.offset == (2.5, 0.0, -1.5)
    bx, by, bz = viewmodel.offset_inches_to_blender(*classic.offset)
    assert (bx, by, bz) == pytest.approx((-0.0635, 0.0, -0.0381), abs=1e-6)
    assert viewmodel.blender_to_offset_inches(bx, by, bz) == pytest.approx(classic.offset, abs=1e-6)

    desktop = viewmodel.get_preset("desktop")
    assert desktop.fov == 60.0
    assert desktop.offset == (1.0, 1.0, -1.0)
    assert viewmodel.offset_inches_to_blender(*desktop.offset) == pytest.approx((-0.0254, -0.0254, -0.0254), abs=1e-6)

    assert viewmodel.detect_preset(68.0, 2.5, 0.0, -1.5) == "classic"
    assert viewmodel.detect_preset(60.0, 1.0, 1.0, -1.0) == "desktop"
    assert viewmodel.detect_preset(64.0, 2.5, 0.0, -1.5) == "custom"
    assert viewmodel.detect_preset(68.0, 0.0, 0.0, 0.0) == "custom"
    assert viewmodel.get_preset("nonexistent").key == "classic"


# --- Viewmodel Clip Filtering -----------------------------------------------


def test_viewmodel_clip_filtering_excludes_chicken_and_egg():
    clips = [
        "animation/anims/viewmodel/rifle/rifle_ak/idle_ak",
        "animation/anims/viewmodel/rifle/rifle_ak/reload_ak",
        "animation/anims/viewmodel/rifle/_default_rifle/idle_rifle",
        "animation/anims/viewmodel/rifle/_default_rifle/draw_rifle",
        "animation/anims/viewmodel/knife/knife_butterfly/lookat01_butterfly",
        "animation/anims/viewmodel/arms/idle_arms",
        "animation/anims/viewmodel/chicken/chicken_arms_idle",
        "animation/anims/viewmodel/egg/egg_arms_idle",
        "animation/anims/world/rifle/rifle_ak/run_n_ak",
    ]
    ak = Weapon("ak47", "weapons/models/ak47/weapon_rif_ak47.vmdl_c", "rif", "AK-47")

    # Without a weapon: all viewmodel arm clips, excluding world, chicken and egg
    all_vm = viewmodel.clips_for_fp_weapon(clips, None)
    assert len(all_vm) == 6
    assert not any("chicken" in c or "egg" in c or "/world/" in c for c in all_vm)

    # With AK-47: own folder first, then non-replaced _default_rifle (draw_rifle, not idle_rifle), then arms
    ak_clips = viewmodel.clips_for_fp_weapon(clips, ak)
    assert ak_clips == [
        "animation/anims/viewmodel/rifle/rifle_ak/idle_ak",
        "animation/anims/viewmodel/rifle/rifle_ak/reload_ak",
        "animation/anims/viewmodel/rifle/_default_rifle/draw_rifle",
        "animation/anims/viewmodel/arms/idle_arms",
    ]

    # With census: honours explicit skeleton field
    census = {
        "clips": {
            "animation/anims/viewmodel/rifle/rifle_ak/idle_ak": {"skeleton": clipdata.VIEWMODEL},
            "animation/anims/viewmodel/rifle/rifle_ak/reload_ak": {"skeleton": clipdata.WORLDMODEL},
        }
    }
    assert viewmodel.is_viewmodel_arm_clip("animation/anims/viewmodel/rifle/rifle_ak/idle_ak", census)
    assert not viewmodel.is_viewmodel_arm_clip("animation/anims/viewmodel/rifle/rifle_ak/reload_ak", census)


def test_viewmodel_preview_bones():
    vm_parents = {
        "root_motion": None,
        "wpn": "root_motion",
        "wpnTip": "wpn",
        "armUpperShoulder_L": "root_motion",
        "arm_lower_L": "armUpperShoulder_L",
        "hand_L": "arm_lower_L",
        "armUpperStraighten_0_L": "arm_lower_L",
        "arm_upper_L": "armUpperStraighten_0_L",
        "finger_index_0_L": "hand_L",
        "armUpperShoulder_R": "root_motion",
        "arm_lower_R": "armUpperShoulder_R",
        "hand_R": "arm_lower_R",
        "armUpperStraighten_0_R": "arm_lower_R",
        "arm_upper_R": "armUpperStraighten_0_R",
    }
    bones = clipdata.preview_bones(vm_parents)
    assert bones == list(clipdata.VIEWMODEL_PREVIEW_BONES)
    assert "finger_index_0_L" not in bones


def test_clipbrowse_viewmodel_mode():
    clips = [
        cb.Clip("animation/anims/viewmodel/rifle/rifle_ak/idle_ak"),
        cb.Clip("animation/anims/viewmodel/knife/knife_butterfly/lookat01_butterfly"),
        cb.Clip("animation/anims/viewmodel/chicken/chicken_arms_idle"),
        cb.Clip("animation/anims/world/rifle/rifle_ak/run_n_ak"),
    ]
    tree = cb.build_tree(clips, mode="viewmodel")
    keys = [node.key for node in tree]
    assert "viewmodel" in keys
    assert "viewmodel/rifle" in keys
    assert "viewmodel/knife" in keys
    assert cb.UNUSABLE_NODE in keys

    all_node = tree[0]
    visible_all = cb.clips_in(clips, all_node, "")
    assert [c.name for c in visible_all] == ["idle_ak", "lookat01_butterfly"]

    assert cb.default_node(tree, "rifle", mode="viewmodel") == keys.index("viewmodel/rifle")
    assert cb.held_folder(clips, "rifle", "ak47", mode="viewmodel") == "rifle_ak"


# --- Target Detection for First-Person Arms ---------------------------------


def _make_fp_rig(agent_key: str = "ctm_sas/ctm_sas"):
    fp_root = FakeObj("ctm_sas.fp_root", "EMPTY", **{targets.ROLE_PROP: targets.FIRSTPERSON_ROOT_ROLE})
    pov_cam = FakeObj("ctm_sas.pov_camera", "CAMERA", parent=fp_root, **{targets.ROLE_PROP: targets.POV_CAMERA_ROLE})
    arms = FakeObj(
        "ctm_sas.fp_skeleton",
        "ARMATURE",
        parent=fp_root,
        **{targets.ROLE_PROP: targets.VIEWMODEL_ARMS_ROLE, targets.AGENT_KEY_PROP: agent_key},
    )
    sleeves = FakeObj("ctm_sas.firstperson_sleeves", "MESH", parent=arms, **{targets.ROLE_PROP: targets.SLEEVES_ROLE})
    return fp_root, pov_cam, arms, sleeves


def test_fp_arms_target_resolution_and_coexistence():
    fp_root, pov_cam, arms, sleeves = _make_fp_rig()
    wpn_root = FakeObj(
        "ak47.weapon",
        "ARMATURE",
        parent=arms,
        **{targets.ROLE_PROP: targets.WEAPON_ROOT_ROLE, targets.WEAPON_KEY_PROP: "ak47"},
    )
    wpn_body = FakeObj("ak47.body_hd", "MESH", parent=wpn_root, **{targets.ROLE_PROP: targets.WEAPON_BODY_ROLE})
    scene = [fp_root, pov_cam, arms, sleeves, wpn_root, wpn_body]

    # FP arms is NOT a third-person agent, IS viewmodel_arms and character_rig
    assert not targets.is_agent(arms)
    assert targets.is_viewmodel_arms(arms)
    assert targets.is_character_rig(arms)
    assert targets.find_agent_armature(arms, [arms], scene) is None

    # Selecting arms, sleeves, weapon, POV camera, or FirstPersonRoot all resolve to arms!
    for sel in (arms, sleeves, wpn_root, wpn_body, pov_cam, fp_root):
        assert targets.find_viewmodel_arms(sel, [sel], scene) is arms
        assert targets.find_character_rig(sel, [sel], scene) is arms
        assert targets.find_weapon_target(sel, [sel], scene) is arms

    # Held weapon under FP arms is NOT a standalone weapon
    assert not targets.is_standalone_weapon(wpn_root)
    assert targets.held_weapon_root(arms) is wpn_root
    assert targets.held_weapon_key(arms) == "ak47"
    assert targets.weapon_root(arms) is wpn_root
    assert targets.weapon_key(arms) == "ak47"

    # Coexistence with a third-person agent
    tp_agent = FakeObj(
        "sas.skeleton",
        "ARMATURE",
        **{targets.ROLE_PROP: targets.AGENT_ROLE, targets.AGENT_KEY_PROP: "ctm_sas/ctm_sas"},
    )
    mixed = scene + [tp_agent]
    assert targets.find_character_rig(tp_agent, [tp_agent], mixed) is tp_agent
    assert targets.find_character_rig(pov_cam, [pov_cam], mixed) is arms
    # Ambiguous when nothing selected and both exist
    assert targets.find_character_rig(None, [], mixed) is None
    # But find_agent_armature still finds the sole 3P agent, and find_viewmodel_arms finds the sole FP rig
    assert targets.find_agent_armature(None, [], mixed) is tp_agent
    assert targets.find_viewmodel_arms(None, [], mixed) is arms
