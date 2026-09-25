import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from steward.logging import REDACTED, SecretScrubFilter, scrub

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_worker_starts_with_zero_jobs_and_stops_on_sigterm() -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL"}}
    proc = subprocess.Popen(
        [sys.executable, "-m", "worker"],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output = ""
        deadline = time.monotonic() + 15
        assert proc.stdout is not None
        while time.monotonic() < deadline and "scheduler started" not in output:
            output += proc.stdout.readline()
        assert "scheduler started" in output
        assert "0 jobs registered" in output
        proc.send_signal(signal.SIGTERM)
        remaining, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert "scheduler stopped" in remaining
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("Authorization: Basic a2V5OnNlY3JldA==", "a2V5OnNlY3JldA=="),
        ("header Bearer eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJ4In0.sig", "eyJhbGciOiJFUzI1NiJ9"),
        (
            "connect postgresql://app_backend:hunter2pass@db.x.supabase.co:5432/postgres",
            "hunter2pass",
        ),
        ("t212 key is MYKEY123", "MYKEY123"),
    ],
)
def test_scrub_removes_credentials(text: str, leaked: str) -> None:
    cleaned = scrub(text, ["MYKEY123"])
    assert leaked not in cleaned and REDACTED in cleaned


def test_log_filter_scrubs_formatted_args_and_tracebacks() -> None:
    record = logging.LogRecord(
        "t", logging.ERROR, __file__, 1, "using secret %s", ("s3cr3t-value",), None
    )
    try:
        raise RuntimeError("boom with s3cr3t-value")
    except RuntimeError:
        record.exc_info = sys.exc_info()
    SecretScrubFilter(["s3cr3t-value"]).filter(record)
    assert "s3cr3t-value" not in record.getMessage()
    assert record.exc_text is not None and "s3cr3t-value" not in record.exc_text
