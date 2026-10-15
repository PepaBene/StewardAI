"""Single-container entrypoint: `python -m worker.launcher` (what start.sh runs).

Imports the API and the worker code once, then forks one child for each. The children share
the imported modules' memory copy-on-write, which is roughly 80 MB less than two processes
that each import everything. Nothing is opened before the fork (no pool, no event loop), so
it is safe to fork.

The parent only supervises. A crashed worker is restarted after 5 seconds; if the API exits,
the worker is stopped and the container exits with the API's status, so the host restarts both.
SIGTERM/SIGINT are passed on to both children and the parent waits for them. Linux only.
"""

import gc
import os
import signal
import sys
import time
from collections.abc import Callable
from types import FrameType

import uvicorn

from api.main import app
from worker.__main__ import main as run_worker

RESTART_DELAY_SECONDS = 5.0
STOP_GRACE_SECONDS = 30.0


def _run_api() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - the container's only listener, behind the host's proxy
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


def _fork(target: Callable[[], None]) -> int:
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            target()
        except SystemExit as exc:
            os._exit(exc.code if isinstance(exc.code, int) else 1)
        except BaseException:
            import traceback

            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    return pid


def _exit_code(status: int) -> int:
    return os.waitstatus_to_exitcode(status) % 256


def main() -> int:
    # Everything imported so far is long-lived: keep it out of the collector's generations so
    # a collection in a child does not touch (and so copy) those pages.
    gc.collect()
    gc.freeze()

    stopping = False
    asked_to_stop = False  # SIGTERM/SIGINT from the host: a clean stop, not an API failure

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping, asked_to_stop
        stopping = asked_to_stop = True
        for pid in (api_pid, worker_pid):
            if pid:
                try:
                    os.kill(pid, signum)
                except ProcessLookupError:
                    pass

    api_pid = _fork(_run_api)
    worker_pid = _fork(run_worker)
    restart_at: float | None = None
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    exit_code = 0
    deadline: float | None = None
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return exit_code
        if pid == 0:  # nothing has exited; a cleared pid below is also 0, so test this first
            time.sleep(0.5)
        elif pid == api_pid:
            exit_code = _exit_code(status)
            print(f"api exited with status {exit_code}", file=sys.stderr, flush=True)
            api_pid = 0
            if not stopping:
                stopping = True
                if worker_pid:
                    os.kill(worker_pid, signal.SIGTERM)
        elif pid == worker_pid:
            print(f"worker exited with status {_exit_code(status)}", file=sys.stderr, flush=True)
            worker_pid = 0
            if not stopping:
                print(
                    f"restarting worker in {RESTART_DELAY_SECONDS:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                restart_at = time.monotonic() + RESTART_DELAY_SECONDS
        if stopping:
            if not (api_pid or worker_pid):
                return 0 if asked_to_stop else exit_code
            deadline = deadline or time.monotonic() + STOP_GRACE_SECONDS
            if time.monotonic() > deadline:
                for left in (api_pid, worker_pid):
                    if left:
                        os.kill(left, signal.SIGKILL)
        elif restart_at is not None and not worker_pid and time.monotonic() >= restart_at:
            worker_pid = _fork(run_worker)
            restart_at = None


if __name__ == "__main__":
    sys.exit(main())
