"""Read node transforms straight from a .glb file. No Blender imports here.

Used where the exact game transform of a bone is needed: Blender's glTF importer
reorients bones, so a bone's Blender rest matrix is not its game transform.
All matrices are 4x4 row-major nested lists in glTF space (Y up).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

Matrix4 = list[list[float]]
IDENTITY: Matrix4 = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def read_json(path: Path) -> dict:
    with open(path, "rb") as handle:
        magic, _version, _length = struct.unpack("<4sII", handle.read(12))
        if magic != b"glTF":
            raise ValueError(f"{path.name} is not a .glb file.")
        chunk_length, chunk_type = struct.unpack("<I4s", handle.read(8))
        if chunk_type != b"JSON":
            raise ValueError(f"{path.name} does not start with a JSON chunk.")
        return json.loads(handle.read(chunk_length))


def multiply(a: Matrix4, b: Matrix4) -> Matrix4:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def local_matrix(node: dict) -> Matrix4:
    if "matrix" in node:
        m = node["matrix"]  # column-major in the file
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    tx, ty, tz = node.get("translation", (0.0, 0.0, 0.0))
    x, y, z, w = node.get("rotation", (0.0, 0.0, 0.0, 1.0))
    sx, sy, sz = node.get("scale", (1.0, 1.0, 1.0))
    return [
        [(1 - 2 * (y * y + z * z)) * sx, (2 * (x * y - z * w)) * sy, (2 * (x * z + y * w)) * sz, tx],
        [(2 * (x * y + z * w)) * sx, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z - x * w)) * sz, ty],
        [(2 * (x * z - y * w)) * sx, (2 * (y * z + x * w)) * sy, (1 - 2 * (x * x + y * y)) * sz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def root_joint_name(data: dict, preferred: str = "") -> str:
    """Name of the skeleton's root bone: the skin joint whose parent is not a joint.

    Some models have extra root bones (the Dual Berettas carry a holster bone);
    `preferred` picks the right one by name when there are several.
    """
    nodes = data.get("nodes", [])
    skins = data.get("skins", [])
    if not skins:
        raise ValueError("The file has no skeleton.")
    joints = set(skins[0]["joints"])
    children = {child for index in joints for child in nodes[index].get("children", [])}
    roots = [index for index in skins[0]["joints"] if index not in children]
    names = [nodes[index]["name"] for index in roots]
    if len(names) == 1:
        return names[0]
    if preferred in names:
        return preferred
    raise ValueError(f"Expected one root bone, found {len(names)}.")


def node_world_matrix(data: dict, name: str) -> Matrix4:
    """World rest matrix of the named node, following its parents to the scene root."""
    nodes = data.get("nodes", [])
    parent = {child: index for index, node in enumerate(nodes) for child in node.get("children", [])}
    index = next((i for i, node in enumerate(nodes) if node.get("name") == name), None)
    if index is None:
        raise KeyError(name)
    matrix = IDENTITY
    while index is not None:
        matrix = multiply(local_matrix(nodes[index]), matrix)
        index = parent.get(index)
    return matrix


_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_FLOAT = 5126


def read_glb(path: Path) -> tuple[dict, bytes]:
    """The JSON and binary chunks of a .glb file."""
    data = Path(path).read_bytes()
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError(f"{Path(path).name} is not a .glb file.")
    offset, document, binary = 12, None, b""
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<I4s", data, offset)
        chunk = data[offset + 8: offset + 8 + chunk_length]
        if chunk_type == b"JSON":
            document = json.loads(chunk)
        elif chunk_type == b"BIN\x00":
            binary = chunk
        offset += 8 + chunk_length
    if document is None:
        raise ValueError(f"{Path(path).name} has no JSON chunk.")
    return document, binary


def write_glb(path: Path, document: dict, binary: bytes) -> None:
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text
    if binary:
        chunks += struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks)


def read_floats(document: dict, binary: bytes, accessor_index: int) -> list[tuple[float, ...]]:
    """One float accessor as a list of tuples, one per element."""
    accessor = document["accessors"][accessor_index]
    if accessor.get("componentType") != _FLOAT:
        raise ValueError("only float accessors are read")
    width = _COMPONENTS[accessor["type"]]
    view = document["bufferViews"][accessor["bufferView"]]
    start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    stride = view.get("byteStride") or 4 * width
    return [struct.unpack_from(f"<{width}f", binary, start + i * stride) for i in range(accessor["count"])]


def continuous_rotations(keys: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
    """Flip quaternions that land on the far side of the previous key.

    q and -q are the same rotation, but interpolating between keys of opposite sign
    spins the long way round.
    """
    result = []
    for key in keys:
        if result and sum(a * b for a, b in zip(result[-1], key)) < 0:
            key = tuple(-v for v in key)
        result.append(key)
    return result


def animation_names(document: dict) -> list[str]:
    return [animation.get("name", "") for animation in document.get("animations", [])]


REST_TOLERANCE = 0.001  # metres a bone's rest place may differ between a weapon model and a clip


def nodes_below(document: dict, name: str) -> list[int]:
    """Indices of every node under the first node called `name`, not counting that node."""
    nodes = document.get("nodes", [])
    start = next((i for i, node in enumerate(nodes) if node.get("name") == name), None)
    found, stack = [], list(nodes[start].get("children", [])) if start is not None else []
    while stack:
        index = stack.pop()
        found.append(index)
        stack.extend(nodes[index].get("children", []))
    return found


def _rest_offset(node: dict) -> list[float]:
    matrix = local_matrix(node)
    return [matrix[r][3] for r in range(3)]


def bones_below(document: dict, clip_document: dict, root: str) -> set[int]:
    """The clip's nodes under `root` that the model also has under its own `root`.

    Used for a held weapon: the clip carries the weapon's skeleton next to the agent's.
    The root itself is left out, since it follows the agent's weapon bone. When a
    shared bone rests somewhere else on the model, the clip was made for another
    weapon that happens to use the same bone names, and nothing is returned.
    """
    model_nodes = document.get("nodes", [])
    clip_nodes = clip_document.get("nodes", [])
    model = {model_nodes[i].get("name", ""): model_nodes[i] for i in nodes_below(document, root)}
    found = set()
    for index in nodes_below(clip_document, root):
        node = clip_nodes[index]
        own = model.get(node.get("name", ""))
        if own is None:
            continue
        if any(abs(a - b) > REST_TOLERANCE for a, b in zip(_rest_offset(node), _rest_offset(own))):
            return set()
        found.add(index)
    return found


def graft_animation(rig: Path, clip: Path, out: Path, name: str, below: str = "") -> int:
    """Write `out`: the rig .glb with its animations replaced by the clip's, matched by bone name.

    Animation channels hold each bone's own translation, rotation and scale, so they
    mean the same on any model built on the clip's skeleton. Bones the rig lacks
    (attachment helpers, a weapon's skeleton) are left out. With `below`, only the
    bones under that bone are copied (see bones_below). Returns the channel count.
    """
    document, binary = read_glb(rig)
    clip_document, clip_binary = read_glb(clip)
    nodes = {}
    for index, node in enumerate(document.get("nodes", [])):
        nodes.setdefault(node.get("name", ""), index)
    clip_nodes = clip_document.get("nodes", [])
    wanted = bones_below(document, clip_document, below) if below else None
    data = bytearray(binary)
    data += b"\x00" * (-len(data) % 4)
    accessors = document.setdefault("accessors", [])
    views = document.setdefault("bufferViews", [])
    copied: dict[tuple[int, bool], int] = {}

    def copy(accessor_index: int, rotation: bool) -> int:
        if (accessor_index, rotation) in copied:
            return copied[(accessor_index, rotation)]
        source = clip_document["accessors"][accessor_index]
        values = read_floats(clip_document, clip_binary, accessor_index)
        if rotation:
            values = continuous_rotations(values)
        width = _COMPONENTS[source["type"]]
        packed = struct.pack(f"<{len(values) * width}f", *(v for value in values for v in value))
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(packed)})
        data.extend(packed)
        accessor = {"bufferView": len(views) - 1, "componentType": _FLOAT, "count": len(values), "type": source["type"]}
        if "min" in source and "max" in source and not rotation:
            accessor["min"], accessor["max"] = source["min"], source["max"]
        accessors.append(accessor)
        copied[(accessor_index, rotation)] = len(accessors) - 1
        return copied[(accessor_index, rotation)]

    animations = []
    for animation in clip_document.get("animations", []):
        channels, samplers = [], []
        for channel in animation.get("channels", []):
            target = channel.get("target", {})
            node_name = clip_nodes[target["node"]].get("name", "") if "node" in target else ""
            if node_name not in nodes or (wanted is not None and target.get("node") not in wanted):
                continue
            sampler = animation["samplers"][channel["sampler"]]
            rotation = target.get("path") == "rotation"
            samplers.append({
                "input": copy(sampler["input"], False),
                "output": copy(sampler["output"], rotation),
                "interpolation": sampler.get("interpolation", "LINEAR"),
            })
            channels.append({"sampler": len(samplers) - 1, "target": {"node": nodes[node_name], "path": target["path"]}})
        if channels:
            animations.append({"name": name, "channels": channels, "samplers": samplers})
    if len(animations) > 1:  # one clip makes one action; keep the longest
        animations = [max(animations, key=lambda a: len(a["channels"]))]
    document["animations"] = animations
    if not document.get("buffers"):
        document["buffers"] = [{"byteLength": 0}]
    document["buffers"][0]["byteLength"] = len(data)
    write_glb(out, document, bytes(data))
    return sum(len(a["channels"]) for a in animations)
