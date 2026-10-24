import os
import signal
import subprocess
import threading
import time
from collections import deque
from typing import Callable

import psutil

from app.domain.models import Cancelled, DownloadError


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            children = psutil.Process(process.pid).children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            return
        process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


class ProcessRunner:
    def run(
        self,
        arguments: list[str],
        cancelled: threading.Event,
        *,
        timeout: int = 3600,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        if cancelled.is_set():
            raise Cancelled("Cancelled.")
        options = (
            {
                "creationflags": subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
            }
            if os.name == "nt"
            else {"start_new_session": True}
        )
        try:
            process = subprocess.Popen(
                arguments,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **options,
            )
        except OSError as error:
            raise DownloadError(
                "A required media tool could not be started. Check setup."
            ) from error
        lines: deque[str] = deque(maxlen=200)
        callback_errors: list[Exception] = []

        def read():
            try:
                for line in process.stdout:
                    lines.append(line)
                    if on_line:
                        on_line(line.rstrip())
            except Exception as error:
                callback_errors.append(error)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if cancelled.wait(0.1):
                    raise Cancelled("Cancelled.")
                if time.monotonic() > deadline:
                    raise DownloadError("The operation timed out. Try a shorter video.")
                if callback_errors:
                    raise callback_errors[0]
            reader.join(timeout=10)
            if cancelled.is_set():
                raise Cancelled("Cancelled.")
            if callback_errors:
                raise callback_errors[0]
            output = "".join(lines)
            if process.returncode != 0:
                # Media URLs can contain credentials; callers translate failures into public messages.
                raise DownloadError(
                    "The external media tool could not complete this operation."
                )
            return output
        finally:
            stop_process_tree(process)
            reader.join(timeout=5)
            process.stdout.close()
