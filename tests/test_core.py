from pathlib import Path

from core import catalog, converter, steam
from core.cache import Cache

LISTING = """\
agents/models/ctm_sas/ctm_sas.vmdl_c CRC:00d9a1dcca size:563655
agents/models/tm_phoenix/tm_phoenix_varianth.vmdl_c CRC:0018489924 size:563748
agents/models/shared/arms/glove_hydra/glove_hydra.vmdl_c CRC:00dac45f25 size:454705
animation/anims/world/rifle/rifle_xm1014/shoot_xm1014.vnmclip_c CRC:009c1c7cdc size:4576
animation/anims/world/rifle/rifle_xm1014/shoot_xm1014.vnmclip+non_additive.vnmclip_c CRC:00aa size:10
animation/anims/viewmodel/knife/draw.vnmclip_c CRC:00bb size:20
--- some log line
"""


def test_parse_listing_skips_log_lines():
    entries = converter.parse_listing(LISTING)
    assert len(entries) == 6
    assert entries[0] == converter.PackageEntry("agents/models/ctm_sas/ctm_sas.vmdl_c", 563655)


def test_agents_exclude_shared_parts():
    agents = catalog.agents_from_entries(converter.parse_listing(LISTING))
    assert [a.key for a in agents] == ["ctm_sas/ctm_sas", "tm_phoenix/tm_phoenix_varianth"]
    assert [a.side for a in agents] == ["CT", "T"]


def test_clips_drop_generated_variants():
    clips = catalog.clips_from_entries(converter.parse_listing(LISTING))
    assert clips == [
        "animation/anims/viewmodel/knife/draw",
        "animation/anims/world/rifle/rifle_xm1014/shoot_xm1014",
    ]
    assert catalog.clip_label(clips[1]) == "world/rifle/rifle_xm1014/shoot_xm1014"


def test_library_paths_unescape_backslashes():
    vdf = '"0" { "path"  "C:\\\\Program Files (x86)\\\\Steam" }\n"1" { "path" "D:\\\\SteamLibrary" }'
    assert steam.parse_library_paths(vdf) == [Path(r"C:\Program Files (x86)\Steam"), Path(r"D:\SteamLibrary")]


def test_install_version_and_validity(tmp_path):
    content = tmp_path / "game" / "csgo"
    content.mkdir(parents=True)
    install = steam.GameInstall(tmp_path)
    assert not install.is_valid()
    (content / "pak01_dir.vpk").write_bytes(b"")
    (content / "steam.inf").write_text("ClientVersion=2000942\nPatchVersion=1.41.9.6\n")
    assert install.is_valid()
    assert install.version() == "1.41.9.6-2000942"
    assert steam.find_install(tmp_path) == install
    assert steam.find_install(tmp_path / "missing") is None


def test_cache_paths_are_flat_and_stable(tmp_path):
    cache = Cache(tmp_path, "1.0-1")
    assert cache.model_dir("ctm_sas/ctm_sas").name == "ctm_sas__ctm_sas"
    a = cache.clip_dir("ctm_sas/ctm_sas", "animation/anims/world/x")
    assert a == cache.clip_dir("ctm_sas/ctm_sas", "animation/anims/world/x")
    assert a != cache.clip_dir("ctm_sas/ctm_sas", "animation/anims/world/y")


def test_glb_node_world_matrix_follows_parents(tmp_path):
    import json
    import struct

    from core import glb

    half = 0.5**0.5
    data = {
        "nodes": [
            {"name": "root", "children": [1], "translation": [1, 2, 3], "rotation": [0, half, 0, half]},
            {"name": "wpn", "translation": [1, 0, 0], "scale": [2, 2, 2]},
        ]
    }
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    path = tmp_path / "x.glb"
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(payload)) + struct.pack("<I4s", len(payload), b"JSON") + payload)

    matrix = glb.node_world_matrix(glb.read_json(path), "wpn")
    # Parent turns +90 degrees about Y, so the child's +X offset lands on -Z.
    assert [round(matrix[r][3], 6) for r in range(3)] == [1, 2, 2]
    # Child X axis (scaled by 2) points along -Z in the world.
    assert [round(matrix[r][0], 6) for r in range(3)] == [0, 0, -2]
    data["skins"] = [{"joints": [1, 0]}]
    assert glb.root_joint_name(data) == "root"
    two_roots = {"nodes": [{"name": "weapon"}, {"name": "holster"}], "skins": [{"joints": [0, 1]}]}
    assert glb.root_joint_name(two_roots, "weapon") == "weapon"
    try:
        glb.root_joint_name(two_roots)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    try:
        glb.node_world_matrix(data, "missing")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_weapons_list_only_holdable_models():
    listing = converter.parse_listing(
        "weapons/models/ak47/weapon_rif_ak47.vmdl_c CRC:00 size:1\n"
        "weapons/models/ak47/weapon_rif_ak47_mag.vmdl_c CRC:00 size:1\n"
        "weapons/models/knife/knife_karambit/weapon_knife_karambit.vmdl_c CRC:00 size:1\n"
        "weapons/models/grenade/molotov/weapon_molotov_broken_glass/piece1.vmdl_c CRC:00 size:1\n"
        "weapons/models/grenade/molotov/weapon_molotov_broken_glass.vmdl_c CRC:00 size:1\n"
        "weapons/models/shared/arms/weapon_arms.vmdl_c CRC:00 size:1\n"
        "weapons/models/shared/shells/ak47/ak47_casing.vmdl_c CRC:00 size:1\n"
    )
    weapons = catalog.weapons_from_entries(listing)
    assert [(w.key, w.kind) for w in weapons] == [("knife_karambit", "knife"), ("ak47", "rif")]
    assert weapons[1].model_path == "weapons/models/ak47/weapon_rif_ak47.vmdl_c"


def test_download_rejects_wrong_hash_and_unsafe_paths(tmp_path):
    import zipfile

    from core import download

    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as bundle:
        bundle.writestr(converter.CLI_NAME, b"exe")
        bundle.writestr("lib/extra.dll", b"dll")
    dest = tmp_path / "tools" / "cli"
    dest.parent.mkdir()

    try:
        download.extract_verified(good, "0" * 64, dest)
    except download.DownloadError:
        assert not dest.exists()
    else:
        raise AssertionError("a wrong hash must be rejected")

    download.extract_verified(good, download.sha256_of(good), dest)
    assert (dest / converter.CLI_NAME).read_bytes() == b"exe"
    assert (dest / "lib" / "extra.dll").is_file()
    assert not dest.with_name("cli.partial").exists()

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr(converter.CLI_NAME, b"exe")
        bundle.writestr("../outside.txt", b"x")
    try:
        download.extract_verified(evil, download.sha256_of(evil), tmp_path / "tools" / "cli2")
    except download.DownloadError:
        assert not (tmp_path / "tools" / "outside.txt").exists()
    else:
        raise AssertionError("an archive escaping its folder must be rejected")

    url, name, digest = download.asset_for_platform("win32")
    assert url.endswith(f"/{download.CLI_VERSION}/cli-windows-x64.zip") and len(digest) == 64
    try:
        download.asset_for_platform("darwin")
    except download.DownloadError:
        pass
    else:
        raise AssertionError("unsupported platforms must be reported")


def test_export_requires_a_clip(tmp_path):
    conv = converter.Converter(tmp_path / "missing.exe")
    try:
        conv.export_model(tmp_path / "p.vpk", "a/b.vmdl_c", tmp_path, [], materials=False)
    except converter.ConverterError as exc:
        assert "at least one clip" in str(exc)
    else:
        raise AssertionError("expected ConverterError")


def test_cache_is_keyed_by_converter_and_lists_stale_folders(tmp_path):
    from core.cache import folder_size

    cache = Cache(tmp_path, "1.0-1", "abcd1234")
    assert cache.version_dir.name == "1.0-1-abcd1234"
    assert cache.map_dir("de_dust2") == cache.version_dir / "maps" / "de_dust2"
    assert cache.stale_dirs() == []
    cache.version_dir.mkdir(parents=True)
    old = cache.cache_dir / "0.9-1"
    old.mkdir()
    (old / "x.bin").write_bytes(b"12345")
    assert cache.stale_dirs() == [old]
    assert folder_size(old) == 5


def test_export_reuses_finished_work_and_cleans_up_after_failure(tmp_path):
    conv = converter.Converter(tmp_path / "missing.exe")
    assert conv.tag() == "unknown"
    out = tmp_path / "out"
    try:
        conv.export(tmp_path / "p.vpk", "maps/x.vmap_c", out)
    except converter.ConverterError:
        pass
    else:
        raise AssertionError("a converter that cannot start must be reported")
    assert not out.exists() and not list(tmp_path.glob("out.partial*"))

    done = converter.exported_path(out, "maps/x.vmap_c")
    assert done == out / "maps" / "x.glb"
    done.parent.mkdir(parents=True)
    done.write_bytes(b"glb")
    assert conv.export(tmp_path / "p.vpk", "maps/x.vmap_c", out) == done


def test_clips_are_matched_to_the_held_weapon():
    knife = catalog.Weapon("knife_bayonet", "weapons/models/knife/knife_bayonet/weapon_knife_bayonet.vmdl_c", "knife")
    awp = catalog.Weapon("awp", "weapons/models/awp/weapon_snip_awp.vmdl_c", "snip")
    smoke = catalog.Weapon("smokegrenade", "weapons/models/grenade/smokegrenade/weapon_smokegrenade.vmdl_c", "other")
    bomb = catalog.Weapon("c4", "weapons/models/c4/weapon_c4.vmdl_c", "other")
    assert [catalog.clip_family(w) for w in (knife, awp, smoke, bomb)] == ["knife", "rifle", "grenade", "equipment"]
    run = "world/rifle/_default_rifle/run_n_rifle"
    assert catalog.family_of_clip(run) == "rifle"
    assert catalog.family_of_clip("animation/anims/" + run) == "rifle"
    assert catalog.family_of_clip("viewmodel/knife/draw") == ""
    assert catalog.clip_fits(run, awp) and not catalog.clip_fits(run, knife)
    assert catalog.clip_fits("world/shared/flashed/flashed", knife)
    assert catalog.clip_fits(run, None)


def test_maps_list_playable_maps_first(tmp_path):
    for name in ("de_dust2", "de_dust2_vanity", "cs_office", "graphics_settings", "workshop_preview_dust2"):
        (tmp_path / f"{name}.vpk").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    found = catalog.maps_in(tmp_path)
    assert [m.name for m in found] == ["cs_office", "de_dust2", "de_dust2_vanity", "workshop_preview_dust2"]
    assert [m.is_backdrop for m in found] == [False, False, True, True]
    assert found[1].world_path == "maps/de_dust2.vmap_c"
    assert catalog.maps_in(tmp_path / "missing") == []
