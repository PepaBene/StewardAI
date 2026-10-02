"""Mask anything that looks like a credential in free text a provider, a job or an exception
wrote, before it is stored or shown (provider_fetch_audit.error_redacted, provider_access.detail,
job and job-step errors, the source read models).

Rules, in order:
- `Bearer x` / `Basic x` authorization values;
- credentials in a URL's userinfo (`https://user:pass@host`, `https://token@host`);
- JSON/dict members whose quoted key names a credential, whatever the value's length
  (`"key": "x"`, `'api_token': 'x'`);
- `name=value` / `name: value` where the name is a credential name, including env-style names
  (`FRED_API_KEY=x`), header-style names (`subscription-key`, `x-api-key`) and URL-encoded
  forms (`api%5Fkey%3Dx`, `token%3Dx%26a%3Db`);
- query parameters called `key` or `token`;
- any other long opaque token (32+ characters), unless it is plain snake/kebab-case words.

A bare word such as "token expired" or "invalid key length" is not a credential: a name only
counts when it is followed by `=` or `:` (or its URL-encoded form).
"""

import re

REDACTED = "[redacted]"

_SEP = r"(?:_|-|%5[Ff]|%2[Dd])?"
# Credential names, case-insensitive; matched whole (a trailing/leading word char disqualifies).
_NAME_CORE = (
    rf"api{_SEP}key|apikey|api{_SEP}token|api{_SEP}secret|access{_SEP}token|auth{_SEP}token"
    rf"|refresh{_SEP}token|id{_SEP}token|session{_SEP}token|bearer{_SEP}token"
    rf"|subscription{_SEP}key|ocp-apim-subscription-key|client{_SEP}secret|secret{_SEP}key"
    rf"|private{_SEP}key|secret|password|passwd|pwd|authorization|x-api-key|token"
)
# Env-style: any UPPER_SNAKE name ending in a credential word (FRED_API_KEY, GH_TOKEN).
_ENV_NAME = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)"
_NAME = rf"(?:{_ENV_NAME}|(?i:{_NAME_CORE}))"
_NAME_PREFIXED = rf"(?:[A-Za-z0-9]+[_-])?{_NAME}"  # e.g. fred_api_key, my-service-token

_AUTH = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;\"'&]+")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@?#\"']+@")
_QUOTED_MEMBER = re.compile(
    rf"""(?P<k>(?P<q>["'])(?:{_NAME_PREFIXED}|key)(?P=q)\s*:\s*)"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,}\]]+)""",
    re.IGNORECASE,
)
_ASSIGN = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<k>{_NAME_PREFIXED}(?:\"|')?(?:\s*[=:]\s*|%3[Dd]|%3[Aa]))"
    r"""(?:"[^"]*"|'[^']*'|(?:%(?!26)|[^\s,;&"'}\]%])+)"""
)
_QUERY = re.compile(r"(?i)(?P<k>[?&](?:key|token|apikey)(?:=|%3D))[^&\s\"']+")
_LONG = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9_\-])")
_WORDS = re.compile(r"^[A-Za-z]+(?:[_-][A-Za-z]+)+$")


def _long(m: re.Match[str]) -> str:
    return m.group(0) if _WORDS.match(m.group(0)) else REDACTED


def redact_text(text: str) -> str:
    out = _AUTH.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    out = _USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", out)
    out = _QUOTED_MEMBER.sub(lambda m: f"{m.group('k')}{m.group('q')}{REDACTED}{m.group('q')}", out)
    out = _ASSIGN.sub(lambda m: f"{m.group('k')}{REDACTED}", out)
    out = _QUERY.sub(lambda m: f"{m.group('k')}{REDACTED}", out)
    return _LONG.sub(_long, out)


def redact(text: str | None) -> str | None:
    return None if text is None else redact_text(text)
