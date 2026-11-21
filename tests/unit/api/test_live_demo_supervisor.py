import os
import signal
import sys
import time

from scripts.live_demo import ManagedProcess


def test_terminated_component_is_restarted_and_only_owned_process_is_stopped(tmp_path):
    with (tmp_path / "worker.log").open("wb") as log:
        worker = ManagedProcess(
            "test-worker",
            [sys.executable, "-c", "import time; time.sleep(60)"],
            tmp_path,
            dict(os.environ),
            log,
        )
        try:
            worker.tick(0)
            original = worker.process
            assert original is not None
            os.kill(original.pid, signal.SIGTERM)
            original.wait(timeout=3)
            worker.tick(1)
            assert worker.process is None
            worker.tick(2)
            restarted = worker.process
            assert restarted is not None and restarted.pid != original.pid
            assert restarted.poll() is None
        finally:
            worker.stop()
        assert restarted.poll() is not None


def test_stop_cleans_owned_children_when_the_process_leader_has_exited(tmp_path):
    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    child = (
        "import signal,sys,time; from pathlib import Path; "
        f"signal.signal(signal.SIGTERM, lambda *_: (Path({str(stopped)!r}).touch(), sys.exit(0))); "
        f"Path({str(ready)!r}).touch(); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"\nwhile not Path({str(ready)!r}).exists(): time.sleep(.01)"
    )
    with (tmp_path / "worker.log").open("wb") as log:
        worker = ManagedProcess(
            "parent", [sys.executable, "-c", parent], tmp_path, dict(os.environ), log
        )
        worker.tick(0)
        assert worker.process is not None
        try:
            worker.process.wait(timeout=5)
            worker.stop()
            deadline = time.monotonic() + 2
            while not stopped.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert stopped.exists(), "stop left the owned child running"
        finally:
            try:
                os.killpg(worker.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_the_station_starts_in_standby_unless_asked_to_start_live():
    from scripts.live_demo import agent_live_args, live_switch_environment

    assert live_switch_environment({"A": "1"}, start_live=False) == {
        "A": "1",
        "LIVE_STARTS_ON": "false",
    }
    assert live_switch_environment({}, start_live=True)["LIVE_STARTS_ON"] == "true"
    assert agent_live_args(start_live=False) == ["--await-live"]
    assert agent_live_args(start_live=True) == []
