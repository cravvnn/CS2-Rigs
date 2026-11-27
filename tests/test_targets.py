"""Tests for agent and standalone weapon target resolution."""

from core import targets


class FakeObj(dict):
    """Minimal stand-in for bpy.types.Object: dict custom properties plus type, name, parent and children."""

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


def _make_agent(name: str = "sas.skeleton", key: str = "ctm_sas/ctm_sas"):
    armature = FakeObj(
        name,
        "ARMATURE",
        **{targets.AGENT_KEY_PROP: key, targets.ROLE_PROP: targets.AGENT_ROLE},
    )
    body = FakeObj(f"{name}.body", "MESH", parent=armature)
    return armature, body


def _make_weapon(key: str = "ak47", parent=None, *, with_roles: bool = True, legacy: bool = True):
    root_props = {targets.WEAPON_KEY_PROP: key}
    if with_roles:
        root_props[targets.ROLE_PROP] = targets.WEAPON_ROOT_ROLE
    root = FakeObj(f"{key}.weapon", "ARMATURE", parent=parent, **root_props)
    hd_props = {targets.ROLE_PROP: targets.WEAPON_BODY_ROLE, targets.BODY_KIND_PROP: "current"} if with_roles else {}
    hd = FakeObj(f"{key}.weapon_body_hd", "MESH", parent=root, **hd_props)
    leg = None
    if legacy:
        leg_props = {targets.ROLE_PROP: targets.WEAPON_BODY_ROLE, targets.BODY_KIND_PROP: "legacy"} if with_roles else {}
        leg = FakeObj(f"{key}.weapon_body_legacy", "MESH", parent=root, **leg_props)
    return root, hd, leg


def test_standalone_weapon_is_not_mistaken_for_agent():
    root, hd, leg = _make_weapon("ak47")
    assert not targets.is_agent(root)
    assert targets.is_weapon_root(root)
    assert targets.is_standalone_weapon(root)
    assert targets.weapon_root(hd) is root
    assert targets.weapon_key(root) == "ak47"
    assert targets.weapon_key(hd) == "ak47"
    assert targets.weapon_objects(root) == [root, hd, leg]


def test_held_weapon_is_not_standalone_until_unparented():
    agent, _ = _make_agent()
    root, hd, _ = _make_weapon("ak47", parent=agent)
    assert targets.is_weapon_root(root)
    assert not targets.is_standalone_weapon(root)
    assert targets.held_weapon_root(agent) is root
    assert targets.held_weapon_key(agent) == "ak47"
    assert targets.weapon_root(agent) is root
    assert targets.weapon_key(agent) == "ak47"
    assert root in targets.weapon_objects(agent) and hd in targets.weapon_objects(agent)

    # Unparenting turns a held weapon into a standalone weapon automatically.
    agent._children.remove(root)
    root.parent = None
    assert targets.is_standalone_weapon(root)
    assert targets.held_weapon_root(agent) is None


def test_standalone_weapon_parented_to_empty_stays_standalone():
    turntable = FakeObj("Turntable", "EMPTY")
    root, hd, _ = _make_weapon("knife_karambit", parent=turntable, legacy=False)
    assert targets.is_standalone_weapon(root)
    assert targets.standalone_weapon_above(hd) is root
    assert targets.find_standalone_weapon(hd, [hd], [turntable, root, hd]) is root


def test_find_weapon_target_empty_scene():
    cam = FakeObj("Camera", "CAMERA")
    assert targets.find_weapon_target(None, [], []) is None
    assert targets.find_weapon_target(cam, [cam], [cam]) is None


def test_find_weapon_target_sole_standalone_without_selection():
    cam = FakeObj("Camera", "CAMERA")
    root, hd, leg = _make_weapon("ak47")
    scene = [cam, root, hd, leg]
    assert targets.find_weapon_target(None, [], scene) is root
    assert targets.find_weapon_target(cam, [cam], scene) is root


def test_find_weapon_target_multiple_standalone_requires_selection():
    ak_root, ak_hd, ak_leg = _make_weapon("ak47")
    knife_root, knife_hd, _ = _make_weapon("knife_karambit", legacy=False)
    scene = [ak_root, ak_hd, ak_leg, knife_root, knife_hd]
    assert targets.find_weapon_target(None, [], scene) is None
    assert targets.find_weapon_target(ak_hd, [ak_hd], scene) is ak_root
    assert targets.find_weapon_target(knife_hd, [knife_hd], scene) is knife_root


def test_find_weapon_target_agent_and_standalone_coexist():
    agent, agent_body = _make_agent()
    held_root, held_hd, held_leg = _make_weapon("m4a4", parent=agent)
    solo_root, solo_hd, solo_leg = _make_weapon("ak47")
    cam = FakeObj("Camera", "CAMERA")
    scene = [agent, agent_body, held_root, held_hd, held_leg, solo_root, solo_hd, solo_leg, cam]

    # Selecting nothing or an unrelated object keeps the sole agent as default.
    assert targets.find_weapon_target(None, [], scene) is agent
    assert targets.find_weapon_target(cam, [cam], scene) is agent

    # Selecting the agent, its body, or its held weapon targets the agent.
    assert targets.find_weapon_target(agent, [agent], scene) is agent
    assert targets.find_weapon_target(agent_body, [agent_body], scene) is agent
    assert targets.find_weapon_target(held_hd, [held_hd], scene) is agent

    # Selecting the standalone weapon or its mesh targets the standalone weapon.
    assert targets.find_weapon_target(solo_root, [solo_root], scene) is solo_root
    assert targets.find_weapon_target(solo_hd, [solo_hd], scene) is solo_root
    assert targets.find_weapon_target(cam, [cam, solo_hd], scene) is solo_root


def test_find_weapon_target_two_agents_one_standalone():
    agent1, _ = _make_agent("a1")
    agent2, _ = _make_agent("a2")
    solo_root, solo_hd, _ = _make_weapon("knife_butterfly", legacy=False)
    scene = [agent1, agent2, solo_root, solo_hd]

    # Agents are ambiguous when unselected, so the sole standalone weapon wins.
    assert targets.find_weapon_target(None, [], scene) is solo_root
    # Selecting either agent targets that agent.
    assert targets.find_weapon_target(agent2, [agent2], scene) is agent2


def test_select_weapon_bodies_survives_renames_and_old_files():
    root, hd, leg = _make_weapon("ak47", with_roles=True)
    hd.name = "HeroGun_Main"
    leg.name = "HeroGun_Old"
    assert targets.select_weapon_bodies([hd, leg]) == (leg, hd)

    # Older files without BODY_KIND_PROP fall back to names.
    old_root, old_hd, old_leg = _make_weapon("ak47", with_roles=False)
    assert targets.select_weapon_bodies([old_hd, old_leg]) == (old_leg, old_hd)

    # Knives have only one body.
    knife_root, knife_body, _ = _make_weapon("knife_karambit", with_roles=True, legacy=False)
    knife_body[targets.BODY_KIND_PROP] = "part"
    knife_body.name = "MyKarambit"
    assert targets.select_weapon_bodies([knife_body]) == (None, knife_body)
