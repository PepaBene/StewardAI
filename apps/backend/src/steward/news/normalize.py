"""Normalising news items: canonical URLs, outlet hosts, headline keys for near-duplicate
matching, and published times.

The canonical URL is the dedupe key (`news_item.url` is unique): tracking parameters,
fragments, default ports and trailing slashes are removed so the same article reached through
two feeds is stored once.
"""

import datetime as dt
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from steward.documents.text import html_to_text

_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z_]+|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|ocid|cmpid|taid|guccounter|"
    r"guce_referrer|guce_referrer_sig|smid|mod|ref|ref_src|feedtype|rss|cid|__twitter_impression|"
    r"yptr|ncid|soc_src|soc_trk)$",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = (
        host
        if port is None or (scheme, port) in (("http", 80), ("https", 443))
        else (f"{host}:{port}")
    )
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING_PARAMS.match(k)
    )  # fmt: skip
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host or None


# Wire-service prefixes on the same story: "UPDATE 2-", "EXCLUSIVE-", "Breaking:".
_PREFIX = re.compile(
    r"^\s*((update|wrapup|corrected|refile|rpt|exclusive|breaking|insight|factbox|analysis|"
    r"live|urgent|flash|correction|update\s+\d+)(\s+\d+)?\s*[-:\u2013\u2014|]\s*)+",
    re.IGNORECASE,
)
_KNOWN_OUTLETS = (
    "reuters", "associated press", "ap", "ap news", "cnbc", "marketwatch", "bloomberg",
    "financial times", "ft", "wall street journal", "wsj", "the wall street journal",
    "yahoo finance", "yahoo", "business wire", "businesswire", "pr newswire", "globenewswire",
    "seeking alpha", "benzinga", "barron's", "barrons", "investing.com", "the motley fool",
    "motley fool", "fox business", "cnn", "cnn business", "bbc", "bbc news", "forbes",
    "hospodářské noviny", "e15", "seznam zprávy", "zonebourse", "marketscreener", "nasdaq",
)  # fmt: skip
_SEPARATORS = r"\s+[-|\u2013\u2014:]\s+"

_ABBREVIATIONS = {
    "bln": "billion", "bn": "billion", "b": "billion", "mln": "million", "mn": "million",
    "m": "million", "pct": "percent", "%": "percent", "q1": "q1", "yr": "year", "co": "company",
    "corp": "", "corporation": "", "inc": "", "plc": "", "ltd": "", "sa": "", "ag": "",
    "nv": "", "se": "",
    "&": "and", "u.s.": "us", "u.s": "us", "usa": "us",
}  # fmt: skip
_STOPWORDS = frozenset(
    "a an the of to in on for and or as at by with from its it is are was were be been has have "
    "had will would could should may might says said say over after amid into about than that this "
    "their his her they them s new".split()
)


def strip_outlet(headline: str, outlet_names: tuple[str, ...] = ()) -> str:
    """Remove a trailing " - Reuters" / " | CNBC" style outlet suffix and wire prefixes."""
    text = _PREFIX.sub("", headline).strip()
    names = {n.casefold() for n in (*_KNOWN_OUTLETS, *outlet_names) if n}
    for _ in range(2):
        match = re.search(rf"{_SEPARATORS}([^-|\u2013\u2014]{{1,60}})$", text)
        if match and match.group(1).strip().casefold().rstrip(".") in names:
            text = text[: match.start()].strip()
            continue
        match = re.search(r"\s*\(([^()]{1,40})\)\s*$", text)
        if match and match.group(1).strip().casefold() in names:
            text = text[: match.start()].strip()
            continue
        break
    return text


def headline_tokens(headline: str, outlet_names: tuple[str, ...] = ()) -> list[str]:
    """Content words of a headline, normalised: what near-duplicate matching compares."""
    text = unicodedata.normalize("NFKC", strip_outlet(headline, outlet_names)).casefold()
    text = text.replace("\u2019", "'").replace("'s ", " ")
    # "$2 billion", "$2bln", "2.5%": split digits from units so both spellings match.
    text = re.sub(r"(\d)([a-z%]+)\b", r"\1 \2", text)
    raw = re.findall(r"[a-z0-9À-ɏ]+(?:[.,][0-9]+)?|%", text)
    tokens = []
    for token in raw:
        token = token.replace(",", "")
        token = _ABBREVIATIONS.get(token, token)
        if token and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


def headline_key(headline: str, outlet_names: tuple[str, ...] = ()) -> str:
    return " ".join(headline_tokens(headline, outlet_names))


def as_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def clean_text(text: str | None, limit: int = 1000) -> str | None:
    """Plain text from a feed summary (tags stripped, whitespace collapsed, capped)."""
    if not text:
        return None
    plain = html_to_text(text) if "<" in text else text
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return None
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "\u2026"
