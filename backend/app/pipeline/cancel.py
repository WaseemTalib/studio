"""Cooperative cancellation for pipeline jobs.

The runner binds a Job to its worker thread. Pipeline code calls check() between steps,
sleep() while waiting, and run_process() for subprocesses, so cancel() can stop the job
promptly: running ffmpeg/yt-dlp processes are killed and waits end early. Requests that are
already in flight to an API finish first; the job stops right after.
"""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class Cancelled(Exception):
    """The user cancelled the job."""


class Job:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._procs: set[subprocess.Popen] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def start(self) -> bool:
        """Mark the job as running; False if it was cancelled while still queued."""
        with self._lock:
            if self._event.is_set():
                return False
            self._started = True
            return True

    def cancel(self) -> bool:
        """Request cancellation and kill running subprocesses. True if the job had started,
        in which case the worker records the outcome; otherwise it will never run."""
        with self._lock:
            self._event.set()
            procs = list(self._procs)
            started = self._started
        for proc in procs:
            _kill(proc)
        return started


_local = threading.local()


@contextmanager
def bound(job: Job) -> Iterator[None]:
    _local.job = job
    try:
        yield
    finally:
        _local.job = None


def _current() -> Job | None:
    return getattr(_local, "job", None)


def check() -> None:
    job = _current()
    if job is not None and job.cancelled:
        raise Cancelled()


def sleep(seconds: float) -> None:
    job = _current()
    if job is None:
        time.sleep(seconds)
    elif job._event.wait(seconds):
        raise Cancelled()


def run_process(cmd: list[str], timeout: float | None = None,
                on_line: Callable[[str], None] | None = None) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output=True, text=True) that cancel() can kill, children included.

    With on_line, each stdout line is passed to it as it arrives (timeout is then ignored);
    if on_line raises, for example Cancelled, the process is killed."""
    check()
    job = _current()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    if job is not None:
        with job._lock:
            job._procs.add(proc)
            if job.cancelled:  # cancelled between check() and registration
                _kill(proc)
    try:
        if on_line is None:
            out, err = proc.communicate(timeout=timeout)
        else:
            out, err = _stream(proc, on_line)
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.communicate()
        raise
    except BaseException:
        _kill(proc)
        proc.wait()
        raise
    finally:
        if job is not None:
            with job._lock:
                job._procs.discard(proc)
    check()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _stream(proc: subprocess.Popen, on_line: Callable[[str], None]) -> tuple[str, str]:
    err: list[str] = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)  # keep the pipe drained
    reader.start()
    out = []
    for line in proc.stdout:
        out.append(line)
        on_line(line)
    proc.wait()
    reader.join()
    return "".join(out), "".join(err)


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)  # its own session: yt-dlp's ffmpeg goes too
    except (ProcessLookupError, PermissionError):
        pass
