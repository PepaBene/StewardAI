"""Object-key normalisation and evidence classes shared by the graph writers.

`normalize_object_key` mirrors the SQL function `public.graph_norm_object_key` (the database
applies the SQL one in triggers, so the two must agree; tests compare them on ASCII names).
"""

from __future__ import annotations

import re
from typing import Literal

LEI_RE = re.compile(r"^[0-9A-Z]{18}[0-9]{2}$")
QID_RE = re.compile(r"^wd:q[0-9]+$", re.IGNORECASE)
_LEGAL_FORM = re.compile(
    r" (inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|sa|nv|se|llc|lp|gmbh|bv"
    r"|spa|oyj|ab|asa|holdings?)$"
)


def normalize_object_key(key: str) -> str:
    """LEI and Wikidata ids are kept, any other string is a name with punctuation, case and
    trailing legal-form words removed: "Siemens AG", "SIEMENS A.G." and "name:siemens" agree."""
    s = key.strip()
    if LEI_RE.match(s):
        return s
    if QID_RE.match(s):
        return "wd:" + s[3:].upper()
    s = re.sub(r"^name:", "", s, flags=re.IGNORECASE).lower().replace("&", " and ")
    s = s.replace(".", "").replace("'", "").replace("\u2019", "")  # S.A. -> sa, O'Brien -> obrien
    s = re.sub(r"[\W_]+", " ", s)
    s = " ".join(s.split())
    s = re.sub(r"^the ", "", s)
    for _ in range(4):
        shorter = _LEGAL_FORM.sub("", s).strip()
        if shorter == s:
            break
        s = shorter
    return "name:" + s


def canonical_edge_type(edge_type: str) -> str:
    return "parent" if edge_type == "ultimate_parent" else edge_type


EvidenceClass = Literal["primary", "regulatory", "vendor", "community", "owner_review", "unknown"]

_REGULATORS = frozenset(
    {"sec", "edgar", "esma", "pse", "gleif", "cnb", "ecb", "fca", "bafin", "ted", "epo", "esef"}
)
_ISSUER = frozenset({"issuer", "ir", "company"})
_VENDORS = frozenset(
    {
        "alpha",
        "alphavantage",
        "finnhub",
        "fmp",
        "eodhd",
        "twelve",
        "diffbot",
        "pdl",
        "peopledatalabs",
        "yahoo",
        "stooq",
        "openfigi",
        "news",
    }
)


def evidence_class(regulator: str | None) -> EvidenceClass:
    """The evidence class a stored document's source implies. Matches whole tokens only, so a
    vendor called "secure_data" is not mistaken for the SEC."""
    if not regulator:
        return "unknown"
    tokens = {t for t in re.split(r"[^a-z0-9]+", regulator.casefold()) if t}
    joined = "".join(sorted(tokens))
    if tokens & {"owner", "manual"}:
        return "owner_review"
    if "wikidata" in tokens:
        return "community"
    if tokens & _VENDORS or joined in _VENDORS:
        return "vendor"
    if tokens & _REGULATORS:
        return "regulatory"
    if tokens & _ISSUER:
        return "primary"
    return "unknown"


PRIMARY_CLASSES = frozenset({"primary", "regulatory", "owner_review"})
