import subprocess
import sys

from core import proc


def test_a_cancelled_tracker_kills_its_child():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    tracker = proc.Tracker()
    try:
        with proc.tracked(tracker):
            proc.track(child)
        tracker.cancel()
        assert child.wait(timeout=20) is not None
    finally:
        proc.discard(child)
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_child_added_after_the_cancel_is_killed_at_once():
    tracker = proc.Tracker()
    tracker.cancel()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with proc.tracked(tracker):
            proc.track(child)
        assert child.wait(timeout=20) is not None
    finally:
        proc.discard(child)
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_refused_replace_keeps_the_file_the_other_process_wrote(tmp_path, monkeypatch):
    source = tmp_path / "status.json.1.part"
    target = tmp_path / "status.json"
    source.write_text("mine")
    target.write_text("theirs")

    def refuse(*_args, **_kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(proc.os, "replace", refuse)
    proc.replace_file(source, target)
    assert target.read_text() == "theirs"
    assert not source.exists()

    target.unlink()
    try:
        proc.replace_file(source, target)
    except PermissionError:
        pass
    else:
        raise AssertionError("a refused replace with no target must be reported")


def test_staging_names_are_short(tmp_path):
    name = proc.staging_name("s")
    assert name.startswith(".s") and len(name) < 16
    assert proc.temp_name(tmp_path / "catalog.json").name.startswith("catalog.json.")
    assert proc.temp_name(tmp_path / "catalog.json") != tmp_path / "catalog.json"


def test_staging_names_differ_between_calls():
    assert len({proc.staging_name("s") for _ in range(100)}) == 100


def test_a_pool_task_reports_to_the_job_that_started_it():
    from concurrent.futures import ThreadPoolExecutor

    tracker = proc.Tracker()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with proc.tracked(tracker):
            start = proc.carry(lambda: proc.track(child))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(start).result()
        tracker.cancel()
        assert child.wait(timeout=20) is not None
    finally:
        proc.discard(child)
        if child.poll() is None:
            child.kill()
            child.wait()
