"""Resolve which imported agent or standalone weapon the panel and operators act on. No Blender imports here."""

from __future__ import annotations

AGENT_KEY_PROP = "cs2rig_agent"
WEAPON_KEY_PROP = "cs2rig_weapon"
CLIP_KEY_PROP = "cs2rig_clip"
COMPOSED_PROP = "cs2rig_composed"  # an additive clip shown over the bind pose
INSTANCE_PROP = "cs2rig_instance"  # one imported agent or weapon, so a job still finds it after a rename
ROLE_PROP = "cs2rig_role"  # what an imported object is, found without relying on its name
BODY_KIND_PROP = "cs2rig_body"  # "legacy" or "current" on a weapon body

AGENT_ROLE = "agent"
VIEWMODEL_ARMS_ROLE = "viewmodel_arms"
FIRSTPERSON_ROOT_ROLE = "firstperson_root"
POV_CAMERA_ROLE = "pov_camera"
SLEEVES_ROLE = "firstperson_sleeves"
DEFAULT_GLOVES_ROLE = "default_gloves"
GLOVE_ROLE = "glove"
WEAPON_ROOT_ROLE = "weapon_root"
WEAPON_BODY_ROLE = "weapon_body"

LEGACY_MARK = "legacy"
CURRENT_MARK = "_hd"


def is_agent(obj) -> bool:
    """True for a third-person agent armature (excludes first-person viewmodel arms)."""
    if obj is None or obj.type != "ARMATURE":
        return False
    role = obj.get(ROLE_PROP)
    if role == AGENT_ROLE:
        return True
    if role is not None:
        return False
    return AGENT_KEY_PROP in obj.keys()


def is_viewmodel_arms(obj) -> bool:
    """True for a first-person viewmodel arms armature."""
    return obj is not None and obj.type == "ARMATURE" and obj.get(ROLE_PROP) == VIEWMODEL_ARMS_ROLE


def is_character_rig(obj) -> bool:
    """True for either a third-person agent or a first-person viewmodel arms armature."""
    return is_agent(obj) or is_viewmodel_arms(obj)


def is_firstperson_root(obj) -> bool:
    return obj is not None and obj.get(ROLE_PROP) == FIRSTPERSON_ROOT_ROLE


def is_pov_camera(obj) -> bool:
    return obj is not None and obj.type == "CAMERA" and obj.get(ROLE_PROP) == POV_CAMERA_ROLE


def is_weapon_root(obj) -> bool:
    return obj is not None and (
        obj.get(ROLE_PROP) == WEAPON_ROOT_ROLE or WEAPON_KEY_PROP in obj.keys()
    )


def agent_above(obj):
    while obj is not None:
        if is_agent(obj):
            return obj
        obj = obj.parent
    return None


def viewmodel_arms_for(obj):
    """The first-person arms armature associated with `obj` (itself, an ancestor, or via FirstPersonRoot/POV_Camera)."""
    cur = obj
    while cur is not None:
        if is_viewmodel_arms(cur):
            return cur
        if is_firstperson_root(cur):
            return next((child for child in cur.children_recursive if is_viewmodel_arms(child)), None)
        cur = cur.parent
    return None


def rig_above(obj):
    """The third-person agent or first-person arms armature associated with `obj`."""
    cur = obj
    while cur is not None:
        if is_character_rig(cur):
            return cur
        if is_firstperson_root(cur):
            return next((child for child in cur.children_recursive if is_viewmodel_arms(child)), None)
        cur = cur.parent
    return None


def weapon_root_above(obj):
    while obj is not None:
        if is_weapon_root(obj):
            return obj
        obj = obj.parent
    return None


def is_standalone_weapon(obj) -> bool:
    return is_weapon_root(obj) and rig_above(obj) is None


def standalone_weapon_above(obj):
    root = weapon_root_above(obj)
    return root if root is not None and rig_above(root) is None else None


def held_weapon_root(armature):
    """The held weapon's root object under an agent or first-person rig, which carries its catalogue key."""
    return next((obj for obj in armature.children_recursive if is_weapon_root(obj)), None)


def held_weapon_objects(armature) -> list:
    root = held_weapon_root(armature)
    return [root, *root.children_recursive] if root is not None else []


def held_weapon_key(armature) -> str:
    """The catalogue key of the weapon the rig holds, or '' when its hands are empty."""
    root = held_weapon_root(armature)
    return root.get(WEAPON_KEY_PROP, "") if root is not None else ""


def weapon_root(target):
    """The weapon root for a character rig (its held weapon) or for a weapon object itself."""
    if target is None:
        return None
    if is_character_rig(target):
        return held_weapon_root(target)
    return weapon_root_above(target)


def weapon_objects(target) -> list:
    """The weapon root and all of its descendant objects, for a character rig or a weapon."""
    root = weapon_root(target)
    return [root, *root.children_recursive] if root is not None else []


def weapon_key(target) -> str:
    """The catalogue key of a held weapon or a standalone weapon, or ''."""
    root = weapon_root(target)
    return root.get(WEAPON_KEY_PROP, "") if root is not None else ""


def weapon_body_kind(part: str) -> str:
    return "legacy" if LEGACY_MARK in part else ("current" if CURRENT_MARK in part else "part")


def select_weapon_bodies(meshes) -> tuple:
    """The weapon's old-layout body and its current one; either can be None.

    Files imported by 0.3.0 carry the role on each body, so renaming one does not
    lose it; older files fall back to the names the importer gave them.
    """
    legacy = next((obj for obj in meshes if obj.get(BODY_KIND_PROP) == "legacy"), None)
    current = next((obj for obj in meshes if obj.get(BODY_KIND_PROP) == "current"), None)
    if legacy is None:
        legacy = next((obj for obj in meshes if LEGACY_MARK in obj.name), None)
    if current is None:
        current = next((obj for obj in meshes if CURRENT_MARK in obj.name), None)
    if current is None:
        current = next((obj for obj in meshes if obj is not legacy), None)
    return legacy, current


def find_agent_armature(active, selected, scene_objects):
    """The third-person agent the panel acts on: the active one, else a selected one, else the only one."""
    agent = agent_above(active)
    if agent is not None:
        return agent
    for obj in selected:
        agent = agent_above(obj)
        if agent is not None:
            return agent
    agents = [obj for obj in scene_objects if is_agent(obj)]
    return agents[0] if len(agents) == 1 else None


def find_viewmodel_arms(active, selected, scene_objects):
    """The first-person arms armature the panel acts on: active, else selected, else the only one."""
    arms = viewmodel_arms_for(active)
    if arms is not None:
        return arms
    for obj in selected:
        arms = viewmodel_arms_for(obj)
        if arms is not None:
            return arms
    all_arms = [obj for obj in scene_objects if is_viewmodel_arms(obj)]
    return all_arms[0] if len(all_arms) == 1 else None


def find_character_rig(active, selected, scene_objects):
    """The third-person agent or first-person arms the panel acts on: active, else selected, else the only one."""
    rig = rig_above(active)
    if rig is not None:
        return rig
    for obj in selected:
        rig = rig_above(obj)
        if rig is not None:
            return rig
    rigs = [obj for obj in scene_objects if is_character_rig(obj)]
    return rigs[0] if len(rigs) == 1 else None


def find_standalone_weapon(active, selected, scene_objects):
    """The standalone weapon the panel acts on: the active one, else a selected one, else the only one."""
    weapon = standalone_weapon_above(active)
    if weapon is not None:
        return weapon
    for obj in selected:
        weapon = standalone_weapon_above(obj)
        if weapon is not None:
            return weapon
    weapons = [obj for obj in scene_objects if is_standalone_weapon(obj)]
    return weapons[0] if len(weapons) == 1 else None


def find_weapon_target(active, selected, scene_objects):
    """What the Weapon section acts on: a character rig (for its held weapon) or a standalone weapon.

    An explicit selection wins (active object first, then other selected objects).
    When neither is selected, a sole character rig takes precedence over a sole standalone
    weapon so single-rig scenes keep their default target.
    """
    rig = rig_above(active)
    if rig is not None:
        return rig
    standalone = standalone_weapon_above(active)
    if standalone is not None:
        return standalone
    for obj in selected:
        rig = rig_above(obj)
        if rig is not None:
            return rig
    for obj in selected:
        standalone = standalone_weapon_above(obj)
        if standalone is not None:
            return standalone
    rig = find_character_rig(active, selected, scene_objects)
    if rig is not None:
        return rig
    return find_standalone_weapon(active, selected, scene_objects)
