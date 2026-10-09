"""P6.2 / P6.3: a deterministic, offline extractor for deal terms in 8-K, 6-K and press-release
text.

What it does and does not do:
  * it reads only what the text states, with character offsets for every figure, so a reviewer can
    open the passage; scale words ("2.5 billion") are applied, a per-share price never is;
  * a term the text does not state is a typed unknown (`not_stated`), not zero; a headline "deal
    value" or "premium of 35%" fills no term, because both are derived claims, not stated terms;
  * a sentence that denies or only imagines a deal ("has not received an offer", "may seek to
    acquire") is skipped and listed with its reason;
  * documents are clustered into one transaction by the set of parties and the announcement date, so
    two deals by the same acquirer for different targets stay two transactions.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.disclosure.deals import NUMERIC_TERMS

SCALE = {
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "million": Decimal(10) ** 6,
    "mm": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
}
CURRENCY = {
    "$": "USD", "US$": "USD", "USD": "USD", "C$": "CAD", "CAD": "CAD",
    "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP",
}  # fmt: skip
ITEM_MEANING = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.03": "Creation of a direct financial obligation",
    "3.02": "Unregistered sales of equity securities",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}
MONEY = (
    r"(?P<cur>US\$|C\$|\$|€|£|USD|EUR|GBP|CAD)\s?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<scale>billion|million|thousand|bn|mm|m|k)(?![A-Za-z]))?"
)
SKIP = r"[^.$€£]"
FLAGS = re.IGNORECASE
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"\b(?P<m>{MONTHS})\s+(?P<d>\d{{1,2}}),\s+(?P<y>\d{{4}})\b")
ITEM = re.compile(r"\bItem\s+(\d\.\d{2})\b")

NEGATED = [
    r"\bnot (?:received|entered|made|agreed|reached|signed)\b",
    r"\bno (?:definitive |binding )?(?:agreement|offer|proposal|discussions|negotiations)\b",
    r"\bdeclined to comment\b",
    r"\bdenies\b",
]
HYPOTHETICAL = [
    r"\bif (?:we|the company|it)\b",
    r"\b(?:may|might|could) (?:seek|pursue|consider|enter|acquire|make|explore)\b",
    r"\bno assurance\b",
    r"\bcannot assure\b",
    r"\bpotential (?:acquisitions|transactions)\b",
]
CLOSING = re.compile(
    r"subject to (?:customary )?(?:closing )?conditions|closing (?:of the transaction )?is subject "
    r"to"
    r"|regulatory approval|HSR|approval (?:of|by) (?:the )?(?:\w+ )?(?:stockholders|shareholders)",
    FLAGS,
)
TYPE_HINTS: list[tuple[str, str]] = [
    (r"tender offer", "tender_offer"),
    (r"spin-?\s?off|spin off|separation of", "spin_off"),
    (r"repurchase|buyback|buy-back", "buyback"),
    (r"initial public offering", "ipo"),
    (r"public offering of", "follow_on"),
    (r"senior notes|notes due|term loan|credit facility|refinanc", "debt_issuance"),
    (r"joint venture", "joint_venture"),
    (r"strategic investment|minority investment", "strategic_investment"),
    (r"merger of equals|merge with|plan of merger", "merger"),
    (r"to sell|divest|sale of (?:its|the) ", "divestiture"),
    # "to acquire Y" under a "merger agreement" is still an acquisition: the sentence names who buys
    (r"to acquire|will acquire|acquisition of|to purchase", "acquisition"),
    (r"merger agreement", "merger"),
    (r"supply agreement|master services|commercial agreement", "material_agreement"),
]
CAP = r"[A-Z][A-Za-z0-9&.'\-]*"
NAME = rf"{CAP}(?:\s+{CAP}){{0,4}}(?:,\s+(?:Inc|Corp|Ltd|LLC|plc|N\.V|S\.A|AG|SE|Co)\b\.?)?"
PARTIES = re.compile(
    rf"(?P<a>{NAME})\s+(?:today\s+)?(?:announced|has announced|has agreed|agreed|entered into)"
    rf"[^.]{{0,80}}?\b(?P<verb>to acquire|to purchase|to merge with|to sell|to combine with)"
    rf"\s+(?P<b>{NAME})"
)
ROLES = {
    "to acquire": ("acquirer", "target"),
    "to purchase": ("acquirer", "target"),
    "to sell": ("seller", "target"),
    "to merge with": ("merger_party", "merger_party"),
    "to combine with": ("merger_party", "merger_party"),
}
SUFFIX = {
    "inc",
    "corp",
    "corporation",
    "ltd",
    "limited",
    "plc",
    "llc",
    "co",
    "company",
    "the",
    "ag",
    "se",
    "nv",
    "sa",
}


@dataclass(frozen=True)
class FoundTerm:
    name: str
    state: str  # "stated" or "not_stated"
    value: Decimal | None = None
    currency: str | None = None
    text: str | None = None  # the exact passage
    start: int | None = None  # character offsets into the source text
    end: int | None = None


@dataclass(frozen=True)
class Skipped:
    reason: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Extracted:
    items: tuple[tuple[str, int], ...]
    deal_type: str | None
    parties: tuple[str, ...]
    announced_date: dt.date | None
    unaffected_date: dt.date | None
    currency: str | None
    currency_conflict: bool
    terms: dict[str, FoundTerm]
    closing_conditions: FoundTerm
    skipped: tuple[Skipped, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = ()  # role of each entry of `parties`, read from the sentence

    def values(self) -> dict[str, Decimal]:
        """The stated numeric terms, ready for a TermVersion; unknowns are simply absent."""
        return {
            n: t.value for n, t in self.terms.items() if t.state == "stated" and t.value is not None
        }


def _money(m: re.Match[str]) -> tuple[Decimal, str]:
    v = Decimal(m.group("num").replace(",", ""))
    scale = (m.group("scale") or "").lower()
    return (v * SCALE[scale] if scale else v), CURRENCY[m.group("cur").upper()]


def _sentences(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for sep in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])|\n{2,}", text):
        spans.append((pos, sep.start()))
        pos = sep.end()
    spans.append((pos, len(text)))
    return [(a, b) for a, b in spans if b > a]


def _screen(sentence: str) -> str | None:
    for p in NEGATED:
        if re.search(p, sentence, FLAGS):
            return "denial or absence of an offer or agreement"
    for p in HYPOTHETICAL:
        if re.search(p, sentence, FLAGS):
            return "hypothetical or risk wording"
    return None


def _date(m: re.Match[str]) -> dt.date | None:
    try:
        return dt.datetime.strptime(
            f"{m.group('m')} {m.group('d')} {m.group('y')}", "%B %d %Y"
        ).date()
    except ValueError:
        return None


def _money_rules() -> list[tuple[str, re.Pattern[str]]]:
    def p(s: str) -> re.Pattern[str]:
        return re.compile(s, FLAGS)

    return [
        ("price_per_share", p(rf"{MONEY}\s+(?:in\s+cash\s+)?per\s+(?:ordinary\s+)?share")),
        (
            "termination_fee",
            p(rf"(?:termination|break-?up)\s+fee\s+of\s+(?:approximately\s+)?{MONEY}"),
        ),
        (
            "financing_amount",
            p(
                rf"(?:bridge\s+(?:loan|facility)|committed\s+(?:debt\s+)?financing|debt\s+financing|term\s+loan){SKIP}{{0,60}}?{MONEY}"
            ),
        ),
        (
            "financing_amount",
            p(
                rf"{MONEY}\s+(?:of\s+)?(?:committed\s+)?(?:bridge|debt)\s+(?:financing|facility|loan)"
            ),
        ),
        ("earnout_amount", p(rf"earn-?out{SKIP}{{0,60}}?{MONEY}")),
        ("earnout_amount", p(rf"{MONEY}{SKIP}{{0,30}}earn-?out")),
        ("expected_synergies", p(rf"synerg(?:y|ies){SKIP}{{0,60}}?{MONEY}")),
        ("expected_synergies", p(rf"{MONEY}{SKIP}{{0,40}}synerg")),
        ("cash_amount", p(rf"{MONEY}\s+in\s+cash")),
        ("stock_amount", p(rf"{MONEY}\s+in\s+(?:\w+\s+)?(?:stock|shares)")),
        (
            "debt_amount",
            p(rf"(?:assum\w+|assumption)\s+of\s+(?:approximately\s+)?{MONEY}{SKIP}{{0,20}}?debt"),
        ),
        ("authorized_amount", p(rf"(?:repurchase|buyback){SKIP}{{0,60}}?(?:up\s+to|of)\s+{MONEY}")),
        ("executed_amount", p(rf"(?:repurchased|purchased){SKIP}{{0,60}}?{MONEY}")),
        ("refinanced_amount", p(rf"(?:repay\w*|refinanc\w+|redeem\w*){SKIP}{{0,60}}?{MONEY}")),
        (
            "unaffected_price",
            p(rf"unaffected\s+(?:closing\s+)?(?:share\s+)?price{SKIP}{{0,40}}?{MONEY}"),
        ),
    ]


def _pct_rules() -> list[tuple[str, re.Pattern[str]]]:
    n = r"(\d{1,3}(?:\.\d+)?)"
    return [
        ("exchange_ratio", re.compile(r"exchange ratio of\s+(\d*\.?\d+)", FLAGS)),
        (
            "exchange_ratio",
            re.compile(
                r"(\d*\.\d+|\d+)\s+(?:of a\s+)?(?:common\s+)?shares?\s+of\s+[A-Z][^.,;]{1,60}?\s+"
                r"(?:common\s+stock\s+)?for\s+each\s+(?:outstanding\s+)?(?:ordinary\s+)?share",
                FLAGS,
            ),
        ),
        (
            "stake_pct",
            re.compile(
                rf"{n}%\s+(?:of the\s+(?:outstanding\s+)?(?:shares|equity)|stake|interest|equity "
                rf"interest|ownership)",
                FLAGS,
            ),
        ),
    ]


_MONEY_RULES = _money_rules()
_PER_SHARE = re.compile(rf"{MONEY}\s+(?:in\s+cash\s+)?per\s+(?:ordinary\s+)?share", FLAGS)
PER_SHARE_EXCLUDES = frozenset({"cash_amount", "stock_amount"})
DEAL_WORDS = re.compile(
    r"\b(?:offer\w*|consideration|acquir\w+|merger|tender|purchase\w*)\b", FLAGS
)
DEBT_WORDS = re.compile(
    r"\b(?:notes?|debentures?|coupon|bonds?|per\s+annum|interest\s+rate|loan|credit)\b", FLAGS
)
STAKE_WORDS = re.compile(r"stake|equity|ownership|shares", FLAGS)
_PCT_RULES = _pct_rules()
_MIX = re.compile(
    r"(\d{1,3}(?:\.\d+)?)%\s+(?:in\s+)?cash(?:\s+and|,)\s+(\d{1,3}(?:\.\d+)?)%\s+(?:in\s+)?(?:stock|shares)",
    FLAGS,
)


def guess_type(text: str) -> str | None:
    low = text.lower()
    for pattern, kind in TYPE_HINTS:
        if re.search(pattern, low):
            return kind
    return None


def norm_party(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in SUFFIX)


def extract(text: str) -> Extracted:
    """Extract stated terms from one document. Offsets index into `text` as given."""
    found: dict[str, FoundTerm] = {}
    skipped: list[Skipped] = []
    announce_dates: list[dt.date] = []
    any_dates: list[dt.date] = []
    unaffected_date: dt.date | None = None
    closing = FoundTerm("closing_conditions", "not_stated")
    live: list[str] = []
    price_in_deal_sentence = False
    for a, b in _sentences(text):
        s = text[a:b]
        for dm in DATE.finditer(s):
            d = _date(dm)
            if d:
                any_dates.append(d)
        why = _screen(s)
        if why:
            skipped.append(Skipped(why, a, b, s))
            continue
        live.append(s)
        for dm in DATE.finditer(s):
            d = _date(dm)
            if d and re.search(r"announce|entered into|agreement|today", s, FLAGS):
                announce_dates.append(d)
            if d and re.search(r"unaffected", s, FLAGS):
                unaffected_date = d
        per_share = [m.span() for m in _PER_SHARE.finditer(s)]
        for name, rx in _MONEY_RULES:
            m = None
            for cand in rx.finditer(s):
                # a per-share price is not also a total: "$45.00 in cash per share" is no
                # cash_amount
                if name in PER_SHARE_EXCLUDES and any(
                    cand.start() < pe and ps < cand.end() for ps, pe in per_share
                ):
                    continue
                m = cand
                break
            if not m:
                continue
            if name == "price_per_share":
                # the offer sentence beats an earlier incidental "$X per share"
                deal_sentence = bool(DEAL_WORDS.search(s))
                if name in found and (price_in_deal_sentence or not deal_sentence):
                    continue
                price_in_deal_sentence = deal_sentence
            elif name in found:
                continue
            v, cur = _money(m)
            if name == "price_per_share":
                v = Decimal(m.group("num").replace(",", ""))  # a per-share price is never scaled
            found[name] = FoundTerm(name, "stated", v, cur, m.group(0), a + m.start(), a + m.end())
        for name, rx in _PCT_RULES:
            m = rx.search(s)
            if m and name == "stake_pct" and DEBT_WORDS.search(s) and not STAKE_WORDS.search(m[0]):
                m = None  # "5.25% senior notes", "% interest rate": a coupon, not a stake
            if m and name not in found:
                raw = Decimal(m.group(1))
                v = raw / 100 if name == "stake_pct" else raw
                found[name] = FoundTerm(
                    name, "stated", v, None, m.group(0), a + m.start(), a + m.end()
                )
        mix = _MIX.search(s)
        if mix and "cash_pct" not in found:
            for nm, g in (("cash_pct", 1), ("stock_pct", 2)):
                found[nm] = FoundTerm(
                    nm,
                    "stated",
                    Decimal(mix.group(g)) / 100,
                    None,
                    mix.group(0),
                    a + mix.start(),
                    a + mix.end(),
                )
        if closing.state == "not_stated" and CLOSING.search(s):
            closing = FoundTerm("closing_conditions", "stated", None, None, s.strip(), a, b)
    terms = {n: found.get(n, FoundTerm(n, "not_stated")) for n in NUMERIC_TERMS}
    currencies = {t.currency for t in terms.values() if t.currency and t.state == "stated"}
    joined = " ".join(live)
    pm = PARTIES.search(" ".join(s for s in live))
    parties = (pm.group("a").strip(), pm.group("b").strip().rstrip(".,")) if pm else ()
    return Extracted(
        tuple((m.group(1), m.start()) for m in ITEM.finditer(text)),
        guess_type(joined),
        parties,
        announce_dates[0] if announce_dates else (any_dates[0] if any_dates else None),
        unaffected_date,
        next(iter(currencies)) if len(currencies) == 1 else None,
        len(currencies) > 1,
        terms,
        closing,
        tuple(skipped),
        ROLES.get(pm.group("verb").lower(), ()) if pm else (),
    )


# --------------------------------------------------------------------------------- clustering
@dataclass(frozen=True)
class DocText:
    id: int
    filed: dt.date
    text: str


@dataclass(frozen=True)
class Cluster:
    key: str
    parties: tuple[str, ...]
    announced_date: dt.date
    document_ids: tuple[int, ...]


def cluster_documents(
    docs: Sequence[DocText], *, window_days: int = 14
) -> tuple[list[Cluster], list[int]]:
    """Group documents about one transaction: the same set of parties, announced within
    `window_days` of the group's first document. Returns (clusters, ids that need a reviewer)."""
    groups: dict[frozenset[str], list[tuple[dt.date, int]]] = {}
    review: list[int] = []
    for d in docs:
        ex = extract(d.text)
        names = frozenset(norm_party(p) for p in ex.parties if norm_party(p))
        if len(names) < 2:
            review.append(d.id)
            continue
        groups.setdefault(names, []).append((ex.announced_date or d.filed, d.id))
    out: list[Cluster] = []
    for names, members in groups.items():
        members.sort()
        current: list[tuple[dt.date, int]] = []
        for member in members:
            if current and (member[0] - current[0][0]).days > window_days:
                out.append(_cluster(names, current))
                current = []
            current.append(member)
        if current:
            out.append(_cluster(names, current))
    return sorted(out, key=lambda c: (c.announced_date, c.key)), review


def _cluster(names: frozenset[str], members: list[tuple[dt.date, int]]) -> Cluster:
    ordered = tuple(sorted(names))
    first = members[0][0]
    return Cluster(
        f"txn:{'|'.join(ordered)}@{first.isoformat()}", ordered, first, tuple(i for _, i in members)
    )
