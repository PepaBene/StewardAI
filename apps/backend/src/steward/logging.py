"""Logging setup with secret scrubbing.

Every log record passes through a filter that redacts configured secret values and
credential-shaped substrings (HTTP auth headers, passwords inside connection URLs)
before any handler formats it.
"""

import logging
import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"

_PATTERNS = [
    # Authorization header values: "Basic abc==", "Bearer eyJ..."
    re.compile(r"(?i)\b(basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # Password segment of a URL: scheme://user:password@host
    re.compile(r"(?<=://)([^:/@\s]+):([^@\s]+)(?=@)"),
]


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}:{REDACTED}", text)
    return text


class SecretScrubFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Longest first so a secret that contains another is redacted whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub the template and each argument separately, keeping the args' shape:
        # some formatters (uvicorn's access log) unpack record.args themselves.
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg, self._secrets)
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub_arg(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._scrub_arg(arg) for key, arg in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text, self._secrets)
        return True

    def _scrub_arg(self, arg: object) -> object:
        # Only replace an argument whose text actually contained a secret, so numeric
        # args still satisfy %d-style placeholders.
        text = str(arg)
        cleaned = scrub(text, self._secrets)
        return arg if cleaned == text else cleaned


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    scrubber = SecretScrubFilter(secrets)
    handler.addFilter(scrubber)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own non-propagating handlers before the app is imported;
    # scrub those too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for existing in logging.getLogger(name).handlers:
            existing.addFilter(scrubber)
