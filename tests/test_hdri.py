"""The game's sky HDRIs: which materials count, their names, and the cached extraction."""

import json
import os
import struct
from pathlib import Path

import pytest

from core import hdri
from core.converter import Converter, find_cli

REAL_SKYBOX = [
    "materials/skybox/convoy_sky.vmat_c",
    "materials/skybox/cs_italy_s2_skybox_2.vmat_c",
    "materials/skybox/cs_italy_s2_skybox_2_fog.vmat_c",
    "materials/skybox/cs_italy_s2_skybox_2_lighting.vmat_c",
    "materials/skybox/cs_office_45_0.vmat_c",
    "materials/skybox/hr_dust_ground_sand_skybox.vmat_c",
    "materials/skybox/light_test_sky_default.vmat_c",
    "materials/skybox/light_test_sky_sunset_light.vmat_c",
    "materials/skybox/overpass_airplane_001.vmat_c",
    "materials/skybox/rush_cloudy_01a.vmat_c",
    "materials/skybox/sky_ar_baggage_01.vmat_c",
    "materials/skybox/sky_black.vmat_c",
    "materials/skybox/sky_cbble_sunset.vmat_c",
    "materials/skybox/sky_cs15_daylight01_hdr.vmat_c",
    "materials/skybox/sky_csgo_cloudy01.vmat_c",
    "materials/skybox/sky_de_annubis.vmat_c",
    "materials/skybox/sky_de_dust2.vmat_c",
    "materials/skybox/sky_de_dust2vmat.vmat_c",
    "materials/skybox/sky_de_mirage.vmat_c",
    "materials/skybox/sky_de_nuke.vmat_c",
    "materials/skybox/sky_de_overpass_01.vmat_c",
    "materials/skybox/sky_de_train03.vmat_c",
    "materials/skybox/sky_de_vertigo.vmat_c",
    "materials/skybox/sky_hr_aztec_02.vmat_c",
    "materials/skybox/sky_hr_aztec_02_lighting.vmat_c",
    "materials/skybox/sky_hr_aztec_02_v1.vmat_c",
    "materials/skybox/sky_overcast_01.vmat_c",
    "materials/skybox/sky_rain_night_01.vmat_c",
    "materials/skybox/skymodel_hosekwilkie_ref/skymodel_hosekwilkie_elevation_55_0.vmat_c",
    "materials/skybox/skymodel_hosekwilkie_ref/skymodel_hosekwilkie_elevation_65_0.vmat_c",
    "materials/skybox/test/s2_de_inferno_sky01.vmat_c",
]

MIRAGE_DUMP = """
[1/1] materials/skybox/sky_de_mirage.vmat_c
\tResource Type: Material [Version 1] [Header Version: 12]
\tFile Size: 2076 bytes


--- Resource External Refs: ---
\tId:               Resource Name:
\t0EE5DE0561F55852  materials/skybox/sky_de_mirage_exr_71e5f2a1.vtex


--- Resource Blocks: Count 4 ---
\t-- Block: RERL  Size: 73     bytes [Offset:     64]
\t-- Block: RED2  Size: 1090   bytes [Offset:    144]
\t-- Block: DATA  Size: 522    bytes [Offset:   1248]
\t-- Block: INSG  Size: 300    bytes [Offset:   1776]


--- Data for block "DATA" ---
<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
\tm_materialName = "materials/skybox/sky_de_mirage.vmat"
\tm_shaderName = "sky.vfx"
\tm_intParams = [  ]
\tm_floatParams =
\t[
\t\t{
\t\t\tm_name = "g_flBrightnessExposureBias"
\t\t\tm_flValue = 0.5
\t\t},
\t]
\tm_vectorParams = [  ]
\tm_textureParams =
\t[
\t\t{
\t\t\tm_name = "g_tSkyTexture"
\t\t\tm_pValue = resource:"materials/skybox/sky_de_mirage_exr_71e5f2a1.vtex"
\t\t},
\t]
\tm_dynamicParams = [  ]
\tm_dynamicTextureParams = [  ]
\tm_intAttributes = [  ]
\tm_floatAttributes = [  ]
\tm_vectorAttributes = [  ]
\tm_textureAttributes = [  ]
\tm_stringAttributes = [  ]
\tm_renderAttributesUsed = [  ]
}
"""

NO_TEXTURE_DUMP = MIRAGE_DUMP.replace("g_tSkyTexture", "g_tSomethingElse")


def exr_header(width: int = 2048, height: int = 1024) -> bytes:
    """The smallest bytes `hdri.exr_size` accepts: magic, version and a dataWindow."""
    header = b""
    for name, kind, payload in (
        (b"dataWindow", b"box2i", struct.pack("<4i", 0, 0, width - 1, height - 1)),
        (b"pixelAspectRatio", b"float", struct.pack("<f", 1.0)),
    ):
        header += name + b"\0" + kind + b"\0" + struct.pack("<i", len(payload)) + payload
    return struct.pack("<I", hdri.EXR_MAGIC) + struct.pack("<I", 2) + header + b"\0"


class FakeConverter:
    """Stands in for Source2Viewer-CLI, counting what it is asked for."""

    def __init__(self, dump_text: str = MIRAGE_DUMP, width: int = 2048, height: int = 1024, listing=None):
        self.dump_text = dump_text
        self.width = width
        self.height = height
        self.listing = listing
        self.dumps = 0
        self.exports = 0

    def dump(self, package, path):
        self.dumps += 1
        return self.dump_text

    def export_texture(self, package, path, out_dir, suffix=".png", raw=False):
        self.exports += 1
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (Path(path).stem + suffix)
        target.write_bytes(exr_header(self.width, self.height))
        return target

    def list_package(self, package, path_filter="", extensions=""):
        assert self.listing is not None, "this call should not need the package"
        return [type("Entry", (), {"path": path})() for path in self.listing]


def box(path: str) -> hdri.Skybox:
    skies = hdri.catalogue([path])
    assert len(skies) == 1
    return skies[0]


def test_catalogue_lists_the_real_skies_with_clean_names():
    skies = {sky.key: sky for sky in hdri.catalogue(REAL_SKYBOX)}
    assert skies["dust2"].label == "Dust II"
    assert skies["mirage"].label == "Mirage"
    assert skies["inferno"].label == "Inferno"
    assert skies["nuke"].label == "Nuke"
    assert skies["overpass"].label == "Overpass"
    assert skies["train"].label == "Train"
    assert skies["ancient"].label == "Ancient"
    assert skies["anubis"].label == "Anubis"
    assert skies["vertigo"].label == "Vertigo"
    assert skies["italy"].label == "Italy"
    assert skies["office"].label == "Office"
    assert skies["baggage"].label == "Baggage"
    assert skies["cobblestone"].label == "Cobblestone"
    assert skies["rain_night"].label == "Rain Night"
    # The material de_train actually names, and the older Train sky.
    assert skies["train"].material == "materials/skybox/sky_overcast_01.vmat"
    assert skies["train_alt"].material == "materials/skybox/sky_de_train03.vmat"
    assert skies["inferno"].material == "materials/skybox/test/s2_de_inferno_sky01.vmat"
    assert skies["ancient"].material == "materials/skybox/sky_hr_aztec_02_lighting.vmat"
    assert skies["train"].map == "de_train"
    assert skies["rain_night"].map == ""


def test_catalogue_drops_props_black_skies_tests_and_cube_maps():
    keys = {sky.key for sky in hdri.catalogue(REAL_SKYBOX)}
    materials = {sky.material for sky in hdri.catalogue(REAL_SKYBOX)}
    for dropped in (
        "materials/skybox/sky_black.vmat",
        "materials/skybox/overpass_airplane_001.vmat",
        "materials/skybox/hr_dust_ground_sand_skybox.vmat",
        "materials/skybox/sky_de_dust2vmat.vmat",
        "materials/skybox/cs_italy_s2_skybox_2_fog.vmat",
        "materials/skybox/light_test_sky_default.vmat",
        "materials/skybox/sky_csgo_cloudy01.vmat",
        "materials/skybox/sky_cs15_daylight01_hdr.vmat",
    ):
        assert dropped not in materials
    assert not [sky for sky in materials if "skymodel" in sky]
    assert not [key for key in keys if "black" in key or "airplane" in key]
    assert len(keys) == 20


def test_catalogue_is_ordered_with_unique_keys():
    skies = hdri.catalogue(
        REAL_SKYBOX
        + ["materials/de_cache/sky/de_cache_sky_001.vmat_c"]
        + ["materials/skybox/sky_zeta_test.vmat_c", "materials/skybox/sky_alpha_test.vmat_c"]
    )
    keys = [sky.key for sky in skies]
    assert len(keys) == len(set(keys))
    assert all(key == key.lower() and " " not in key for key in keys)
    named = [sky.key for sky in skies if sky.key in {entry[0] for entry in hdri.SKY_NAMES + hdri.MAP_SKY_NAMES}]
    assert named == [entry[0] for entry in hdri.SKY_NAMES + hdri.MAP_SKY_NAMES]
    # Unknown skies follow the named ones, alphabetically.
    assert [sky.label for sky in skies[len(named) :]] == ["Alpha Test", "Zeta Test"]


def test_catalogue_names_a_sky_it_has_not_seen_before():
    sky = box("materials/skybox/sky_dusty_new_01.vmat_c")
    assert sky.key == "dusty_new_01"
    assert sky.label == "Dusty New 01"
    assert sky.map == ""
    twin = hdri.catalogue(["materials/skybox/sky_dusty_new_01.vmat_c", "materials/skybox/sky_dusty_new_01.vmat"])
    assert {item.key for item in twin} == {"dusty_new_01"}


def test_catalogue_accepts_windows_paths_and_any_extension():
    assert hdri.normalize_material("materials\\skybox\\sky_de_dust2.vmat_c") == "materials/skybox/sky_de_dust2.vmat"
    assert box("materials\\skybox\\sky_de_dust2.vmat_c").label == "Dust II"
    assert hdri.catalogue(["materials/skybox/sky_de_dust2.vtex_c"]) == []
    assert hdri.catalogue(["materials/models/props/chair.vmat_c"]) == []


def test_map_skies_outside_the_skybox_folder_are_listed():
    sky = box("materials/de_cache/sky/de_cache_sky_001.vmat_c")
    assert sky.key == "cache"
    assert sky.label == "Cache"
    assert sky.map == "de_cache"
    # Anything else outside the two skybox folders is not a sky.
    assert hdri.catalogue(["materials/de_cache/concrete/wall.vmat_c"]) == []


def test_label_for_map_uses_the_clean_name_and_falls_back_to_the_map():
    assert hdri.label_for_map("de_dust2") == "Dust II"
    assert hdri.label_for_map("de_dust2_vanity") == "Dust II"
    assert hdri.label_for_map("de_cache") == "Cache"
    assert hdri.label_for_map("de_poseidon") == "de_poseidon"


def test_key_for_material_matches_names_and_suffixes():
    assert hdri.key_for_material("materials/skybox/sky_rain_night_01.vmat") == "rain_night"
    assert hdri.key_for_material("materials/skybox/sky_rain_night_01.vmat_c") == "rain_night"
    assert hdri.key_for_material("materials\\skybox\\sky_de_nuke.vmat") == "nuke"
    assert hdri.key_for_material("materials/skybox/sky_black.vmat") == ""


def test_default_strength_uses_the_materials_own_exposure():
    assert hdri.default_strength(0.0) == pytest.approx(1.0)
    assert hdri.default_strength(1.0) == pytest.approx(2.0)
    assert hdri.default_strength(-1.0) == pytest.approx(0.5)


def test_exr_size_reads_the_data_window(tmp_path):
    path = tmp_path / "sky.exr"
    path.write_bytes(exr_header(4096, 2048))
    assert hdri.exr_size(path) == (4096, 2048)
    path.write_bytes(b"not an exr at all")
    assert hdri.exr_size(path) is None
    assert hdri.exr_size(tmp_path / "missing.exr") is None


def test_extract_exports_the_texture_and_describes_the_sky(tmp_path):
    skybox = box("materials/skybox/sky_de_mirage.vmat_c")
    converter = FakeConverter()
    sky = hdri.extract(converter, Path("pak01_dir.vpk"), skybox, tmp_path / "hdri" / "mirage")
    assert converter.dumps == 1 and converter.exports == 1
    assert sky.key == "mirage"
    assert sky.label == "Mirage"
    assert sky.material == "materials/skybox/sky_de_mirage.vmat"
    assert sky.texture == "materials/skybox/sky_de_mirage_exr_71e5f2a1.vtex"
    assert sky.exposure_bias == pytest.approx(0.5)
    assert sky.strength == pytest.approx(2.0 ** 0.5)
    assert sky.rotation == pytest.approx(-90.0)
    assert sky.is_equirectangular
    assert Path(sky.file).is_file()
    assert Path(sky.file).parent == tmp_path / "hdri" / "mirage"
    manifest = json.loads((tmp_path / "hdri" / "mirage" / hdri.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["version"] == hdri.EXTRACT_VERSION


def test_extract_is_instant_the_second_time(tmp_path):
    skybox = box("materials/skybox/sky_de_mirage.vmat_c")
    folder = tmp_path / "mirage"
    first = hdri.extract(FakeConverter(), Path("pak01_dir.vpk"), skybox, folder)
    lazy = FakeConverter()
    lazy.dump = lambda *args, **kwargs: pytest.fail("the converter was started again")
    lazy.export_texture = lambda *args, **kwargs: pytest.fail("the texture was exported again")
    second = hdri.extract(lazy, Path("pak01_dir.vpk"), skybox, folder)
    assert second == first


def test_extract_rejects_a_material_without_a_sky_texture(tmp_path):
    skybox = box("materials/skybox/sky_de_mirage.vmat_c")
    with pytest.raises(hdri.SkyError, match="equirectangular"):
        hdri.extract(FakeConverter(NO_TEXTURE_DUMP), Path("pak01_dir.vpk"), skybox, tmp_path / "mirage")


def test_extract_rejects_an_image_that_is_not_two_by_one(tmp_path):
    skybox = box("materials/skybox/sky_de_mirage.vmat_c")
    square = FakeConverter(width=1024, height=1024)
    with pytest.raises(hdri.SkyError, match="not a 2:1"):
        hdri.extract(square, Path("pak01_dir.vpk"), skybox, tmp_path / "mirage")


def test_catalogue_is_cached_on_disk(tmp_path):
    folder = tmp_path / "hdri"
    first = hdri.available(folder, FakeConverter(listing=REAL_SKYBOX), Path("pak01_dir.vpk"))
    assert len(first) == 21
    assert "cache" in {sky.key for sky in first}
    assert (folder / hdri.CATALOGUE_NAME).is_file()
    lazy = FakeConverter()
    lazy.list_package = lambda *args, **kwargs: pytest.fail("the package was listed again")
    assert hdri.available(folder, lazy, Path("pak01_dir.vpk")) == first


def test_the_map_specific_skies_are_added_to_every_catalogue(tmp_path):
    skies = hdri.available(tmp_path / "hdri", FakeConverter(listing=REAL_SKYBOX), Path("pak01_dir.vpk"))
    cache = next(sky for sky in skies if sky.key == "cache")
    assert cache.material == "materials/de_cache/sky/de_cache_sky_001.vmat"
    assert cache.map == "de_cache"


def test_a_catalogue_from_an_older_version_is_ignored(tmp_path):
    folder = tmp_path / "hdri"
    folder.mkdir()
    (folder / hdri.CATALOGUE_NAME).write_text(json.dumps({"version": 0, "skies": []}), encoding="utf-8")
    converter = FakeConverter(listing=REAL_SKYBOX)
    assert len(hdri.available(folder, converter, Path("pak01_dir.vpk"))) == 21
    assert converter.list_package is not None


GAME = Path(r"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\csgo")
REAL_PACKAGES = [GAME / "pak01_dir.vpk", GAME / "csgo" / "pak01_dir.vpk"]
DEFAULT_CLI = Path.home() / "AppData/Local/cs2-rigs/tools/source2viewer-21.1/Source2Viewer-CLI.exe"


def real_cli() -> Path | None:
    """The converter this machine has, from the environment or the add-on's cache."""
    return find_cli(os.environ.get("CS2RIG_S2V_CLI")) or (DEFAULT_CLI if DEFAULT_CLI.is_file() else None)


@pytest.mark.skipif(
    not any(package.is_file() for package in REAL_PACKAGES) or real_cli() is None,
    reason="the game files and the converter are not on this machine",
)
def test_the_real_game_lists_its_skies_and_names_them():
    package = next(package for package in REAL_PACKAGES if package.is_file())
    skies = {sky.key: sky for sky in hdri.catalogue_from_package(Converter(real_cli()), package)}
    assert {"dust2", "mirage", "inferno", "nuke", "overpass", "train", "ancient", "anubis", "vertigo", "cache"} <= set(skies)
    assert skies["dust2"].material == "materials/skybox/sky_de_dust2.vmat"
    assert skies["cache"].material == "materials/de_cache/sky/de_cache_sky_001.vmat"
    assert skies["nuke"].label == "Nuke"
    assert "sky_black" not in skies
