"""What a finish is made of, read from the game's material and composite-material files.

Every finish has a composite material: a short recipe that says which paint
material to start from, which values to take from the weapon, and which values
vary with the item's pattern seed. `resolve` follows that recipe and returns the
shader values the compositor needs. No Blender imports here.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from .converter import Converter

LIBRARY_VERSION = 2
PAINT_MATERIALS = "materials/models/weapons/customization/paints/vmats/"
TEMPLATE_MATERIALS = "workshop/paintkits/templates/"
RECIPES = "weapons/paints/"
GLOVE_RECIPES = "gloves/paints/"
GLOVE_MATERIALS = ("gloves/paints/", "gloves/textiles/", "items/assets/paintkits/volatile_02/")
LEGACY_RECIPE = "weapons/paints/legacy/{name}.vcompmat"
SEED_VARIABLE = "g_nRandomSeed"
ALT_SEED_VARIABLE = "g_nRandomSeedAlt"  # used by materials that carry the target's 'seed_offset' attribute
ALT_SEED_STEP = 1000003
WEAR_VARIABLE = "g_flWearAmount"

# Valve's CUniformRandomStream (Park-Miller LCG with a 32-entry shuffle table).
_IA = 16807
_IM = 2147483647
_IQ = 127773
_IR = 2836
_NTAB = 32
_NDIV = 1 + (_IM - 1) // _NTAB
_AM = 1.0 / _IM
_RNMX = 1.0 - 1.2e-7
_AXES = {"x": 0, "y": 1, "z": 2, "w": 3}


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


class UniformRandomStream:
    """Reproduces the game's CUniformRandomStream for paint-seed placement."""

    def __init__(self, seed: int = 0):
        self._idum = 0
        self._iy = 0
        self._iv = [0] * _NTAB
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        seed = ((int(seed) + 0x80000000) & 0xFFFFFFFF) - 0x80000000
        self._idum = seed if seed < 0 else -seed
        self._iy = 0

    def next_int(self) -> int:
        if self._idum <= 0 or not self._iy:
            self._idum = 1 if -self._idum < 1 else -self._idum
            for j in range(_NTAB + 7, -1, -1):
                k = self._idum // _IQ
                self._idum = _IA * (self._idum - k * _IQ) - _IR * k
                if self._idum < 0:
                    self._idum += _IM
                if j < _NTAB:
                    self._iv[j] = self._idum
            self._iy = self._iv[0]
        k = self._idum // _IQ
        self._idum = _IA * (self._idum - k * _IQ) - _IR * k
        if self._idum < 0:
            self._idum += _IM
        j = self._iy // _NDIV
        self._iy = self._iv[j]
        self._iv[j] = self._idum
        return self._iy

    def uniform(self, low: float = 0.0, high: float = 1.0) -> float:
        fl = _f32(_AM * self.next_int())
        if fl > _RNMX:
            fl = _f32(_RNMX)
        return _f32(fl * _f32(high - low) + _f32(low))

_TOKEN = re.compile(
    r"""
    \s*(?:
      (?P<blob>\#\[[^\]]*\])
    | (?P<punct>[{}\[\],=])
    | (?:(?P<prefix>[A-Za-z_]+):)?"(?P<string>(?:[^"\\]|\\.)*)"
    | (?P<word>[^\s{}\[\],="]+)
    )
    """,
    re.VERBOSE,
)


def parse_kv3(text: str):
    """Parse the text form of KeyValues3 the converter prints into dicts, lists and scalars."""
    tokens = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise ValueError(f"unreadable KeyValues3 text at offset {position}")
        position = match.end()
        if match.group("blob") is not None:
            tokens.append(("value", None))
        elif match.group("punct"):
            tokens.append((match.group("punct"), None))
        elif match.group("string") is not None:
            tokens.append(("value", match.group("string").replace('\\"', '"').replace("\\\\", "\\")))
        else:
            tokens.append(("value", _scalar(match.group("word"))))

    def value(i):
        kind, payload = tokens[i]
        if kind == "{":
            out = {}
            i += 1
            while tokens[i][0] != "}":
                key = tokens[i][1]
                if tokens[i][0] != "value" or tokens[i + 1][0] != "=":
                    raise ValueError(f"expected 'key =' near token {i}")
                out[str(key)], i = value(i + 2)
                if tokens[i][0] == ",":
                    i += 1
            return out, i + 1
        if kind == "[":
            out = []
            i += 1
            while tokens[i][0] != "]":
                item, i = value(i)
                out.append(item)
                if tokens[i][0] == ",":
                    i += 1
            return out, i + 1
        if kind == "value":
            return payload, i + 1
        raise ValueError(f"unexpected '{kind}' near token {i}")

    try:
        parsed, _ = value(0)
    except IndexError as exc:
        raise ValueError("KeyValues3 text ends early") from exc
    return parsed


def _scalar(word: str):
    if word in ("true", "false"):
        return word == "true"
    if word == "null":
        return None
    try:
        return int(word)
    except ValueError:
        pass
    try:
        return float(word)
    except ValueError:
        return word


def split_dump(text: str) -> dict[str, dict]:
    """The DATA block of every file in a converter dump of many files, by package path."""
    parts = re.split(r"^\[\d+/\d+\] (\S+)[ \t]*$", text, flags=re.MULTILINE)
    out = {}
    for path, body in zip(parts[1::2], parts[2::2]):
        _, found, data = body.partition('--- Data for block "DATA" ---')
        if not found:
            continue
        data = data.split("--- Data for block", 1)[0]
        data = data.partition("-->")[2] or data  # the header comment has braces of its own
        start = data.find("{")
        if start < 0:
            continue
        try:
            out[path.removesuffix("_c")] = parse_kv3(data[start:])
        except ValueError:
            continue
    return out


def material_values(data: dict) -> dict:
    """A compiled material's values by name; textures are package paths of source textures."""
    values = {}
    for group, field in (("m_intParams", "m_nValue"), ("m_floatParams", "m_flValue"), ("m_vectorParams", "m_value"), ("m_textureParams", "m_pValue"), ("m_stringAttributes", "m_value")):
        for entry in data.get(group) or ():
            value = entry.get(field)
            values[entry["m_name"]] = tuple(value) if isinstance(value, list) else value
    return values


def _variable(entry: dict):
    """A loose variable as (value, low, high); bounds are None where it has none."""
    kind = entry.get("m_nVariableType", "")
    if kind.endswith("BOOLEAN"):
        return int(bool(entry.get("m_bValueBoolean"))), None, None
    if kind.endswith("INTEGER1"):
        return entry.get("m_nValueIntX", 0), None, None
    if kind.endswith("RESOURCE_TEXTURE"):
        return entry.get("m_strTextureRuntimeResourcePath", ""), None, None
    if kind.endswith("RESOURCE_MATERIAL"):
        return entry.get("m_strResourceMaterial", ""), None, None
    if kind.endswith("COLOR4"):
        return tuple(channel / 255.0 for channel in entry.get("m_cValueColor4", (255, 255, 255))), None, None
    if "FLOAT" in kind:
        count = int(kind[-1])
        axes = "XYZW"[:count]
        value = tuple(float(entry.get(f"m_flValueFloat{axis}", 0.0)) for axis in axes)
        low = tuple(float(entry.get(f"m_flValueFloat{axis}_Min", v)) for axis, v in zip(axes, value))
        high = tuple(float(entry.get(f"m_flValueFloat{axis}_Max", v)) for axis, v in zip(axes, value))
        if count == 1:
            return value[0], low[0], high[0]
        return value, low, high
    return entry.get("m_strString", ""), None, None


def _mutator(entry: dict) -> dict:
    kind = entry.get("m_nMutatorCommandType", "").removeprefix("COMP_MAT_PROPERTY_MUTATOR_")
    out = {"do": kind}
    if kind == "COPY_MATCHING_KEYS":
        out["from"] = entry.get("m_strCopyMatchingKeys_InputContainerSrc", "")
    elif kind == "COPY_PROPERTY":
        out["from"] = entry.get("m_strCopyProperty_InputContainerSrc", "")
        out["name"] = entry.get("m_strCopyProperty_InputContainerProperty", "")
        out["to"] = entry.get("m_strCopyProperty_TargetProperty", "")
    elif kind == "SET_VALUE":
        setting = entry.get("m_nSetValue_Value") or {}
        out["to"] = setting.get("m_strName", "")
        out["value"] = _variable(setting)[0]
    elif kind == "GENERATE_TEXTURE":
        out["to"] = entry.get("m_strGenerateTexture_TargetParam", "")
        out["from"] = entry.get("m_strGenerateTexture_InitialContainer", "")
        out["steps"] = [_mutator(step) for step in entry.get("m_vecTexGenInstructions") or ()]
    elif kind == "CONDITIONAL_MUTATORS":
        out["steps"] = [_mutator(step) for step in entry.get("m_vecConditionalMutators") or ()]
    elif kind == "RANDOM_ROLL_INPUT_VARIABLES":
        out["seed"] = entry.get("m_strRandomRollInputVars_SeedInputVar", "")
        out["roll"] = list(entry.get("m_vecRandomRollInputVars_InputVarsToRoll") or ())
    conditions = []
    for condition in entry.get("m_vecConditions") or ():
        conditions.append(
            {
                "test": condition.get("m_nMutatorCondition", "").removeprefix("COMP_MAT_MUTATOR_CONDITION_INPUT_"),
                "in": condition.get("m_strMutatorConditionContainerName", ""),
                "name": condition.get("m_strMutatorConditionContainerVarName", ""),
                "value": str(condition.get("m_strMutatorConditionContainerVarValue", "")),
                "pass": bool(condition.get("m_bPassWhenTrue", True)),
            }
        )
    if conditions:
        out["if"] = conditions
    return out


def recipe(data: dict) -> dict:
    """A composite material reduced to what `resolve` needs, in a form that stores as JSON."""
    out = {"includes": [], "containers": {}, "steps": []}
    for point in data.get("m_Points") or ():
        for procedure in point.get("m_vecCompositeMaterialAssemblyProcedures") or ():
            out["includes"] += [str(path) for path in procedure.get("m_vecCompMatIncludes") or ()]
            for container in procedure.get("m_vecCompositeInputContainers") or ():
                source = container.get("m_nCompositeMaterialInputContainerSourceType", "").removeprefix("CONTAINER_SOURCE_TYPE_")
                entry = {"source": source}
                if source == "SPECIFIC_MATERIAL":
                    entry["material"] = container.get("m_strSpecificContainerMaterial", "")
                elif source == "LOOSE_VARIABLES":
                    entry["variables"] = {v["m_strName"]: list(_variable(v)) for v in container.get("m_vecLooseVariables") or () if "m_strName" in v}
                elif source == "VARIABLE_FROM_TARGET_ATTR":
                    entry["attribute"] = container.get("m_strAttrNameForVar", "")
                out["containers"][container.get("m_strAlias", "")] = entry
            out["steps"] += [_mutator(step) for step in procedure.get("m_vecPropertyMutators") or ()]
    return out


def build_library(converter: Converter, package: Path) -> dict:
    """Every weapon and glove paint material and recipe in the game, read in a few converter runs."""
    materials = {}
    for folder in (PAINT_MATERIALS, TEMPLATE_MATERIALS, *GLOVE_MATERIALS):
        for path, data in split_dump(converter.dump_folder(package, folder, "vmat_c")).items():
            materials[path] = material_values(data)
    recipes = {}
    for folder in (RECIPES, GLOVE_RECIPES):
        recipes.update({path: recipe(data) for path, data in split_dump(converter.dump_folder(package, folder, "vcompmat_c")).items()})
    return {"version": LIBRARY_VERSION, "materials": materials, "recipes": recipes}


def load_or_build_library(converter: Converter, package: Path, cache_file: Path) -> dict:
    try:
        library = json.loads(cache_file.read_text(encoding="utf-8"))
        if library.get("version") == LIBRARY_VERSION:
            return library
    except (OSError, ValueError, AttributeError):
        pass
    library = build_library(converter, package)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(library), encoding="utf-8")
    tmp.replace(cache_file)
    return library


def not_shown(values: dict) -> list[str]:
    """Parts of a resolved finish the compositor leaves out, in words for the user."""
    return []


def recipe_path(kit_name: str, composite_material_path: str = "") -> str:
    return composite_material_path or LEGACY_RECIPE.format(name=kit_name)


def _merged(library: dict, path: str, seen=()) -> dict:
    """A recipe with the recipes it includes; its own containers win over included ones."""
    own = library["recipes"].get(path)
    if own is None:
        raise KeyError(path)
    out = {"containers": {}, "steps": []}

    def add(containers):
        for alias, entry in containers.items():
            old = out["containers"].get(alias)
            if old and old["source"] == entry["source"] == "LOOSE_VARIABLES":
                entry = {"source": "LOOSE_VARIABLES", "variables": {**old["variables"], **entry["variables"]}}
            out["containers"][alias] = entry

    for include in own["includes"]:
        if include in seen or include not in library["recipes"]:
            continue
        inner = _merged(library, include, seen + (path,))
        add(inner["containers"])
        out["steps"] += inner["steps"]
    add(own["containers"])
    out["steps"] += own["steps"]
    return out


def _text(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).lower()


def _passes(step: dict, containers: dict) -> bool:
    for condition in step.get("if", ()):
        container = containers.get(condition["in"])
        if condition["test"] == "CONTAINER_EXISTS":
            met = container is not None
        elif condition["test"] == "CONTAINER_VALUE_EXISTS":
            met = container is not None and condition["name"] in container
        else:
            wanted = {"true": "1", "false": "0"}.get(condition["value"].lower(), condition["value"].lower())
            met = container is not None and condition["name"] in container and _text(container[condition["name"]]) == wanted
        if met != condition["pass"]:
            return False
    return True


def _roll(step: dict, variables: dict, containers: dict, seed: int) -> None:
    """Roll seeded variables inside their bounds with Valve's uniform random stream.

    Variables absent from the container still advance the stream by one draw per
    listed channel, matching how the game evaluates composite recipes.
    """
    seed_var = step.get("seed", "")
    if "." in seed_var:
        seed_alias, seed_name = seed_var.split(".", 1)
        seed = int(containers.get(seed_alias, {}).get(seed_name, seed))
    rng = UniformRandomStream(seed)
    for target in step["roll"]:
        parts = target.split(".")
        alias, name = parts[0], parts[1]
        channels = parts[2] if len(parts) > 2 else ""
        bounds = variables.get(alias, {}).get(name)
        if bounds is None or bounds[1] is None:
            for _ in (channels or "x"):
                rng.uniform(0.0, 1.0)
            continue
        _, low, high = bounds
        if isinstance(low, (list, tuple)):
            if channels:
                current = list(containers[alias][name])
                for axis in channels:
                    index = _AXES[axis]
                    current[index] = rng.uniform(low[index], high[index])
                containers[alias][name] = tuple(current)
            else:
                containers[alias][name] = tuple(rng.uniform(a, b) for a, b in zip(low, high))
        else:
            containers[alias][name] = rng.uniform(low, high)


def resolve(library: dict, path: str, weapon_inputs: dict, weapon_material: dict, seed: int = 0, wear: float = 0.0, attributes: dict | None = None) -> dict:
    """The shader values for one finish on one weapon, or on one glove.

    `weapon_inputs` and `weapon_material` are `material_values` of the weapon's
    composite-inputs material and of its body material. The result has the
    values the colour texture is built from, plus `g_tNormal` when the finish
    replaces the weapon's normal map.

    A glove passes the attributes of its material, `left` and `seed_offset`, as
    `attributes`. Its result keeps the finish's own normal map, and the paint
    values include every texture and number the glove shader needs.
    """
    merged = _merged(library, path)
    containers = {}
    variables = {}
    for alias, entry in merged["containers"].items():
        if entry["source"] == "SPECIFIC_MATERIAL":
            containers[alias] = dict(library["materials"].get(entry.get("material", ""), {}))
        elif entry["source"] == "LOOSE_VARIABLES":
            variables[alias] = entry["variables"]
            containers[alias] = {name: (tuple(v[0]) if isinstance(v[0], list) else v[0]) for name, v in entry["variables"].items()}
        elif entry["source"] == "MATERIAL_FROM_TARGET_ATTR":
            containers[alias] = dict(weapon_inputs)
        elif entry["source"] == "TARGET_MATERIAL":
            containers[alias] = dict(weapon_material)
        elif entry["source"] == "VARIABLE_FROM_TARGET_ATTR" and attributes and entry.get("attribute") in attributes:
            containers[alias] = {entry["attribute"]: attributes[entry["attribute"]]}
        else:
            containers[alias] = {}
    for values in containers.values():
        if SEED_VARIABLE in values:
            values[SEED_VARIABLE] = seed
        if ALT_SEED_VARIABLE in values:
            values[ALT_SEED_VARIABLE] = seed + ALT_SEED_STEP
        if WEAR_VARIABLE in values:
            values[WEAR_VARIABLE] = wear
        named = values.pop("m_strResourceMaterial", None)
        if named:  # a loose variable that names a material stands for that material's values
            values.update(library["materials"].get(named.removesuffix("_c"), {}))

    def run(steps, state):
        for step in steps:
            if not _passes(step, containers):
                continue
            kind = step["do"]
            if kind == "CONDITIONAL_MUTATORS":
                run(step["steps"], state)
            elif kind == "RANDOM_ROLL_INPUT_VARIABLES":
                alias, _, name = step.get("seed", "").partition(".")
                _roll(step, variables, containers, containers.get(alias, {}).get(name, seed))
            elif state is None:
                continue
            elif kind == "COPY_MATCHING_KEYS":
                # "Matching" means matching a shader input. A material only stores the inputs
                # it changes, so the names it holds are not the full list; take them all.
                state.update(containers.get(step["from"], {}))
            elif kind == "COPY_PROPERTY":
                source = containers.get(step["from"], {})
                if step["name"] in source:
                    target, _, name = step["to"].rpartition(".")
                    if target in containers:  # 'instance_params.g_fPatternTexCoordOffset' sets a value inside another container
                        containers[target][name] = source[step["name"]]
                    else:
                        state[step["to"]] = source[step["name"]]
            elif kind == "SET_VALUE":
                state[step["to"]] = step["value"]

    final = {}
    colour = surface = None
    for step in merged["steps"]:
        if step["do"] == "GENERATE_TEXTURE":
            if not _passes(step, containers):
                continue
            state = dict(containers.get(step["from"], {}))
            run(step["steps"], state)
            if step["to"] == "g_tColor" and colour is None:
                colour = state
            elif step["to"] == "g_tMetalness" and surface is None:
                surface = state
        else:
            run([step], final)
    if colour is None:
        raise KeyError(f"{path} has no colour recipe")
    # The surface texture has a recipe of its own, which is where some switches are set.
    for name, value in (surface or {}).items():
        if name.startswith("F_") and name not in ("F_ROUGHNESS_MODE", "F_OUTPUT_MODE"):
            colour[name] = value
    input_normal = colour.get("g_tNormal")
    if final.get("g_tNormal"):
        colour["g_tNormal"] = final["g_tNormal"]
    elif attributes is None:
        colour.pop("g_tNormal", None)
    if input_normal and (colour.get("F_CASE_HARDENING") or colour.get("F_PATINA_AGE")):
        colour["g_tInputNormal"] = input_normal
    return colour
