"""Indoor cubemap probes: ranking, export naming and the extras manifest, without Blender."""

from pathlib import Path

import pytest

from core import maplights
from core.converter import Converter, exported_slices, slice_index


def probe_entity(index, level, size, origin=(0.0, 0.0, 0.0), classname="env_combined_light_probe_volume"):
    half = size / 2.0
    return {
        "classname": classname,
        "array_index": index,
        "indoor_outdoor_level": level,
        "box_mins": [-half, -half, -half],
        "box_maxs": [half, half, half],
        "origin": list(origin),
        "angles": [0.0, 0.0, 0.0],
    }


def test_the_most_indoor_and_largest_probes_come_first():
    entities = [
        probe_entity(1, 2, 200.0, (100.0, 0.0, 0.0)),
        probe_entity(2, 2, 400.0, (200.0, 0.0, 0.0)),
        probe_entity(3, 1, 1000.0, (300.0, 0.0, 0.0)),
        probe_entity(4, -2, 5000.0, (400.0, 0.0, 0.0)),
    ]
    ranked = maplights.cubemap_probes(entities)
    assert [probe["index"] for probe in ranked] == [2, 1, 3, 4]
    assert ranked[0]["volume"] == pytest.approx(400.0**3)


def test_a_probe_index_appears_once_even_when_two_entities_carry_it():
    entities = [
        probe_entity(7, 0, 100.0),
        probe_entity(7, 2, 300.0),
        probe_entity(8, 1, 200.0),
    ]
    ranked = maplights.cubemap_probes(entities)
    assert [probe["index"] for probe in ranked] == [7, 8]
    assert ranked[0]["indoor"] == 2


def test_probes_without_an_array_index_are_skipped():
    entities = [{"classname": "env_cubemap_box", "indoor_outdoor_level": 2}, probe_entity(0, 0, 100.0)]
    assert [probe["index"] for probe in maplights.cubemap_probes(entities)] == [0]


def test_only_the_asked_number_of_probes_is_kept():
    entities = [probe_entity(index, 0, 100.0 + index) for index in range(6)]
    assert [probe["index"] for probe in maplights.cubemap_probes(entities, limit=2)] == [5, 4]


def test_a_probe_becomes_a_calibrated_world_source():
    source = maplights.cubemap_probes([probe_entity(24, 2, 400.0, (952.0, -2296.0, 20.0))])[0]
    probe = maplights.make_probe(source, "probe_24.exr")
    assert probe.index == 24
    assert probe.label == "Cubemap 24 · indoor 2"
    assert probe.indoor == 2
    assert probe.file == "probe_24.exr"
    assert probe.origin == pytest.approx((952.0, -2296.0, 20.0))
    assert probe.strength == pytest.approx(maplights.PROBE_STRENGTH)
    assert probe.rotation == pytest.approx(-90.0)


def test_a_single_slice_array_and_a_long_one_both_name_their_slices():
    assert slice_index(Path("maps/de_mirage/cubemaps/env_cubemap_array_f00.exr"), "env_cubemap_array") == 0
    assert slice_index(Path("maps/de_mirage/cubemaps/env_cubemap_array_f57.exr"), "env_cubemap_array") == 57
    assert slice_index(Path("maps/de_dust2_vanity/cubemaps/env_cubemap_array.exr"), "env_cubemap_array") == 0
    assert slice_index(Path("maps/de_mirage/cubemaps/other_f01.exr"), "env_cubemap_array") is None
    assert slice_index(Path("maps/de_mirage/cubemaps/env_cubemap_array_fxx.exr"), "env_cubemap_array") is None


def test_only_the_wanted_slices_are_copied_out_of_a_texture_array(tmp_path, monkeypatch):
    cli = tmp_path / "Source2Viewer-CLI.exe"
    cli.write_bytes(b"stub")
    converter = Converter(cli)
    package = tmp_path / "de_test.vpk"
    package.write_bytes(b"vpk")
    out_dir = tmp_path / "export" / "probes"
    calls = []

    def fake_run(args, timeout):
        calls.append(args)
        staging = Path(args[args.index("-o") + 1])
        folder = staging / "maps" / "de_test" / "cubemaps"
        folder.mkdir(parents=True)
        for index in range(5):
            (folder / f"env_cubemap_array_f{index:02d}.exr").write_bytes(str(index).encode())
        return ""

    monkeypatch.setattr(converter, "_run", fake_run)
    slices = converter.export_texture_slices(
        package, "maps/de_test/cubemaps/env_cubemap_array.vtex_c", out_dir, keep={1, 3}
    )
    assert set(slices) == {1, 3}
    assert slices[1].read_bytes() == b"1"
    assert not (out_dir / "maps/de_test/cubemaps/env_cubemap_array_f00.exr").exists()
    assert not list(out_dir.parent.glob(".s*"))  # the staging folder is gone
    assert calls

    # A second call reuses the slices already there rather than converting again.
    again = converter.export_texture_slices(
        package, "maps/de_test/cubemaps/env_cubemap_array.vtex_c", out_dir, keep={1, 3}
    )
    assert set(again) == {1, 3}
    assert len(calls) == 1


def test_exported_slices_reads_both_array_naming_schemes(tmp_path):
    folder = tmp_path / "maps" / "de_test" / "cubemaps"
    folder.mkdir(parents=True)
    (folder / "env_cubemap_array.exr").write_bytes(b"0")
    (folder / "env_cubemap_array_f01.exr").write_bytes(b"1")
    (folder / "notes.txt").write_text("not a slice")
    found = exported_slices(tmp_path, "maps/de_test/cubemaps/env_cubemap_array.vtex_c")
    assert {index: file.name for index, file in found.items()} == {
        0: "env_cubemap_array.exr",
        1: "env_cubemap_array_f01.exr",
    }


class FakeConverter:
    """The two converter calls `extract` makes when a map has probes but no sky."""

    def __init__(self, files):
        self.files = files
        self.calls = []

    def dump(self, package, path):
        return "entity dump"

    def export_texture_slices(self, package, path, out_dir, keep=None):
        self.calls.append(path)
        return self.files


def test_extract_carries_probes_in_the_manifest(tmp_path, monkeypatch):
    files = {24: tmp_path / "probe_24.exr", 52: tmp_path / "probe_52.exr"}
    for path in files.values():
        path.write_bytes(b"exr")
    converter = FakeConverter(files)
    entities = [
        probe_entity(24, 2, 400.0, (952.0, -2296.0, 20.0)),
        probe_entity(52, 2, 100.0, (510.0, -2174.0, 28.0)),
    ]
    monkeypatch.setattr(maplights, "parse_entities", lambda text: entities)
    extras = maplights.extract(
        converter,
        Path("de_mirage.vpk"),
        Path("pak01_dir.vpk"),
        "de_mirage",
        tmp_path / "sky",
        tmp_path / "probes",
    )
    assert extras.sky is None
    assert [probe.index for probe in extras.probes] == [24, 52]
    manifest = extras.as_dict()
    assert manifest["version"] == maplights.MANIFEST_VERSION
    assert manifest["probes"][0]["label"] == "Cubemap 24 · indoor 2"
    assert manifest["probes"][0]["file"] == str(files[24])
    assert converter.calls == ["maps/de_mirage/cubemaps/env_cubemap_array.vtex_c"]
