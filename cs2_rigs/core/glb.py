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
