from core import maplib


def test_a_stamp_records_the_game_and_the_package(tmp_path):
    map_dir = tmp_path / "maps" / "de_dust2"
    map_dir.mkdir(parents=True)
    package = tmp_path / "de_dust2.vpk"
    package.write_bytes(b"game")
    library = maplib.library_file(map_dir, "de_dust2")
    assert library == map_dir / "de_dust2.blend"

    # No library: nothing to rebuild.
    assert not maplib.newer_build_possible(map_dir, package, "1.0")
    library.write_bytes(b"blend")
    # A library without a stamp came from an older version that did not write one.
    assert maplib.newer_build_possible(map_dir, package, "1.0")

    maplib.write_stamp(map_dir, maplib.stamp_for(package, "1.0"))
    assert not maplib.newer_build_possible(map_dir, package, "1.0")
    assert maplib.newer_build_possible(map_dir, package, "2.0")

    package.write_bytes(b"game with an update")
    assert maplib.newer_build_possible(map_dir, package, "1.0")


def test_a_migrated_stamp_without_package_details_counts_as_older(tmp_path):
    map_dir = tmp_path / "maps" / "de_dust2"
    map_dir.mkdir(parents=True)
    package = tmp_path / "de_dust2.vpk"
    package.write_bytes(b"game")
    maplib.library_file(map_dir, "de_dust2").write_bytes(b"blend")
    maplib.write_stamp(map_dir, maplib.stamp_for(package, "0.9-1", stats=False))
    assert maplib.newer_build_possible(map_dir, package, "1.0")


def test_moving_a_map_keeps_files_the_target_already_has(tmp_path):
    source = tmp_path / "cache" / "1.0" / "maps" / "de_dust2"
    source.mkdir(parents=True)
    (source / "de_dust2.blend").write_bytes(b"old")
    (source / "new.txt").write_text("moved")
    target = tmp_path / "maps" / "de_dust2"
    target.mkdir(parents=True)
    (target / "de_dust2.blend").write_bytes(b"newer")

    assert maplib.move_map(source, target)
    assert not source.exists()
    assert (target / "de_dust2.blend").read_bytes() == b"newer"
    assert (target / "new.txt").read_text() == "moved"


def test_a_map_that_cannot_be_moved_is_kept(tmp_path, monkeypatch):
    from core import maplib as maplib_mod

    source = tmp_path / "cache" / "1.0" / "maps" / "de_dust2"
    source.mkdir(parents=True)
    (source / "de_dust2.blend").write_bytes(b"old")

    def refuse(*_args, **_kwargs):
        raise OSError("in use")

    monkeypatch.setattr(maplib_mod.shutil, "move", refuse)
    assert not maplib.move_map(source, tmp_path / "maps" / "de_dust2")
    assert (source / "de_dust2.blend").is_file()
