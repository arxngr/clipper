import sys
import threading
import time

import pytest
import psutil

from app.domain.models import Cancelled, DownloadError
from app.infrastructure.process import ProcessRunner


def test_cancellation_terminates_a_running_process():
    event = threading.Event()
    timer = threading.Timer(0.25, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            ProcessRunner().run(
                [sys.executable, "-c", "import time; time.sleep(30)"], event
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5


def test_subprocess_failure_does_not_expose_sensitive_output():
    with pytest.raises(DownloadError) as error:
        ProcessRunner().run(
            [sys.executable, "-c", "print('signed-url-secret'); raise SystemExit(1)"],
            threading.Event(),
        )
    assert "signed-url-secret" not in str(error.value)


def test_cancellation_also_terminates_child_processes():
    event = threading.Event()
    child_pid = []

    def on_line(line):
        child_pid.append(int(line))
        event.set()

    code = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    with pytest.raises(Cancelled):
        ProcessRunner().run([sys.executable, "-c", code], event, on_line=on_line)
    assert child_pid
    try:
        child = psutil.Process(child_pid[0])
        assert child.status() == psutil.STATUS_ZOMBIE or not child.is_running()
    except psutil.NoSuchProcess:
        pass
