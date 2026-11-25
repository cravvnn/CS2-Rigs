import zipfile

import pytest

from core import update


def _release(tag, *names, draft=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "html_url": f"https://github.com/cravvnn/CS2-Rigs/releases/tag/{tag}",
        "assets": [{"name": name, "browser_download_url": f"https://example.invalid/{name}"} for name in names],
    }


def test_versions_sort_by_stage_and_number():
    order = ["0.1.0-alpha", "0.2.0-alpha", "0.2.0-beta", "0.2.0-rc.2", "0.2.0", "0.2.1-alpha", "1.0.0"]
    assert sorted(order, key=update.version_key) == order
    assert update.version_key("v0.2.0-alpha") == update.version_key("0.2.0-alpha")
    with pytest.raises(ValueError):
        update.version_key("latest")


def test_newest_release_skips_drafts_and_releases_without_the_zip():
    releases = [
        _release("v0.3.0-alpha", "CS2_Rigs_0.3.0-alpha_Windows_x64.zip", draft=True),
        _release("v0.2.1-alpha", "CS2_Rigs_0.2.1-alpha_Windows_x64.zip"),
        _release("v0.2.2-alpha", "notes.txt"),
        _release("v0.2.0-alpha", "CS2_Rigs_0.2.0-alpha_Windows_x64.zip"),
    ]
    newest = update.newest_release(releases)
    assert newest.version == "0.2.1-alpha"
    assert newest.page.endswith("/v0.2.1-alpha")
    assert update.is_newer(newest, "0.2.0-alpha")
    assert not update.is_newer(newest, "0.2.1-alpha")
    assert not update.is_newer(newest, "0.3.0-alpha")
    assert update.newest_release([]) is None
    assert not update.is_newer(None, "0.2.0-alpha")


def test_downloaded_package_must_be_this_addon_at_that_version(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as bundle:
        bundle.writestr("blender_manifest.toml", 'id = "cs2_rigs"\nversion = "0.2.1-alpha"\n')
    update.check_package(good, "0.2.1-alpha")
    with pytest.raises(update.UpdateError):
        update.check_package(good, "0.3.0-alpha")
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as bundle:
        bundle.writestr("blender_manifest.toml", 'id = "someone_else"\nversion = "0.2.1-alpha"\n')
    with pytest.raises(update.UpdateError):
        update.check_package(other, "0.2.1-alpha")
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(update.UpdateError):
        update.check_package(broken, "0.2.1-alpha")
