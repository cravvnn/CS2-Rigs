from core import log


def test_private_hides_the_home_folder_in_any_spelling():
    home = r"C:\Users\Someone"
    text = r"C:\Users\Someone\AppData\Local\cs2-rigs and c:/users/someone/Documents/a.blend and D:\Other"
    assert log.private(text, home) == r"~\AppData\Local\cs2-rigs and ~/Documents/a.blend and D:\Other"


def test_private_leaves_text_alone_for_a_bare_drive():
    assert log.private(r"C:\cache", "C:\\") == r"C:\cache"


def test_configure_writes_a_private_log(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    log_file = log.configure(tmp_path / "logs")
    try:
        assert log_file == tmp_path / "logs" / log.LOG_NAME
        assert log.current_file() == log_file
        log.logger.error("could not read %s", home / "Documents" / "scene.blend")
    finally:
        log.close()
    written = log_file.read_text(encoding="utf-8")
    assert "ERROR could not read ~" in written and str(home) not in written
    assert log.current_file() is None


def test_configure_reports_an_unwritable_folder(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert log.configure(blocker / "logs") is None
    assert log.current_file() is None
