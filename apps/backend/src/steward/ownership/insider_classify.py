"""Deterministic economic classification of Section 16 (Forms 3/4/5) transactions.

The Form 4 transaction code says how a transaction was reported, not why it was made. A code
P ("purchase") can be a private placement; a code S ("sale") can be a sale to cover taxes; a
code M is an option exercise whose stock leg is often followed by an S. Presenting the raw
code as conviction would mislabel all of these. This module maps code plus footnote text to
an economic class with a recorded basis, so each row says why it was classified as it was.

Code mapping (SEC Form 4 General Instruction 8 transaction codes):

    P  purchase                          -> open_market_buy   (unless a footnote says private
                                            placement / offering / plan purchase)
    S  sale                              -> open_market_sell  (unless a footnote says
                                            sell-to-cover tax, private / negotiated sale ...)
    M  exercise or conversion of an
       exempt derivative (Rule 16b-3)    -> option_exercise
    X  exercise of in-the-money or
       at-the-money derivative           -> option_exercise
    O  exercise of out-of-the-money
       derivative                        -> option_exercise
    C  conversion of derivative          -> conversion
    A  grant, award or other
       acquisition (Rule 16b-3(d))       -> grant_award
    G  bona fide gift                    -> gift
    F  payment of exercise price or tax
       by delivering / withholding
       securities                        -> tax_withholding
    J  other acquisition or disposition  -> gift / tax_withholding when its footnote says so,
                                            else other
    D  disposition to the issuer         -> other
    I  discretionary transaction under
       Rule 16b-3(f)                     -> other
    K  equity swap                       -> other
    L  small acquisition (Rule 16a-6)    -> other
    U  tender of shares in a change of
       control                           -> other
    W  acquisition or disposition by
       will or the laws of descent       -> other
    Z  deposit into or withdrawal from
       a voting trust                    -> other
    E  expiration of a short derivative  -> other
    H  expiration or cancellation of a
       long derivative                   -> other
    V  voluntarily reported earlier
       than required                     -> other (the underlying code is not carried)
    (initial holding on Form 3/5, no
       transaction)                      -> holding_snapshot

Derivative-table rows are never open-market stock trades: a code P or S on a derivative
(buying or selling an option) is `other`. Only non-derivative rows classified
`open_market_buy` / `open_market_sell` can count towards net discretionary activity.
"""

import datetime as dt
import re
from dataclasses import dataclass

CLASSES = (
    "open_market_buy",
    "open_market_sell",
    "option_exercise",
    "grant_award",
    "gift",
    "tax_withholding",
    "conversion",
    "other",
    "holding_snapshot",
)

CODE_CLASS: dict[str, str] = {
    "P": "open_market_buy",
    "S": "open_market_sell",
    "M": "option_exercise",
    "X": "option_exercise",
    "O": "option_exercise",
    "C": "conversion",
    "A": "grant_award",
    "G": "gift",
    "F": "tax_withholding",
    "J": "other",
    "D": "other",
    "I": "other",
    "K": "other",
    "L": "other",
    "U": "other",
    "W": "other",
    "Z": "other",
    "E": "other",
    "H": "other",
    "V": "other",
}

INTERPRETATION: dict[str, str] = {
    "open_market_buy": "Purchase reported by the insider; the form does not state the motive.",
    "open_market_sell": "Sale reported by the insider; the form does not state the motive "
    "(diversification, taxes, a plan or a view are indistinguishable).",
    "option_exercise": "Exercise of a derivative; compensation mechanics, not a market view.",
    "grant_award": "Equity granted by the issuer; not a market purchase.",
    "gift": "Gift of shares; not a market transaction.",
    "tax_withholding": "Shares withheld or sold to pay tax or an exercise price; mechanical.",
    "conversion": "Conversion of one security into another; not a market transaction.",
    "other": "Reported under a code that is neither a clear open-market trade nor a "
    "compensation mechanic; excluded from net discretionary activity.",
    "holding_snapshot": "Holdings reported on an initial or annual statement; no transaction.",
}

_PRIVATE = re.compile(
    r"private(?:ly)?[- ](?:placement|purchase|sale|offering|negotiated)|privately negotiated"
    r"|public offering|underwritten|initial public offering|\bIPO\b|rights offering"
    r"|employee stock purchase|\bESPP\b|dividend reinvestment|\bDRIP\b"
    r"|pursuant to (?:the )?(?:merger|tender offer|exchange agreement|purchase agreement"
    r"|subscription agreement)|subscription agreement"
    r"|(?:sold|purchased|transferred) (?:to|from) the (?:issuer|company)",
    re.IGNORECASE,
)
_SELL_TO_COVER = re.compile(
    r"(?:sell|sale|sold)[- ]+(?:to|and)[- ]+cover|(?:sold|sale|sell|sells) (?:\w+ ){0,6}to cover"
    r" (?:\w+ ){0,4}(?:tax|withholding)"
    r"|(?:sold|sale|sell|sells) (?:\w+ ){0,6}(?:to|in order to) (?:pay|satisfy|fund|meet)"
    r" (?:\w+ ){0,4}(?:tax|withholding)"
    r"|to satisfy (?:\w+ ){0,4}tax (?:withholding|obligation|liabilit)"
    r"|(?:tax|withholding) (?:obligation|liabilit)\w* (?:in connection|related)",
    re.IGNORECASE,
)
_TAX = re.compile(
    r"tax (?:withholding|obligation|liabilit)\w*|withheld (?:\w+ ){0,6}(?:tax|satisfy)"
    r"|withholding of shares",
    re.IGNORECASE,
)
_GIFT = re.compile(r"\bgift(?:ed|s)?\b|bona fide gift", re.IGNORECASE)

_PLAN = re.compile(r"10b5-?1", re.IGNORECASE)
# An explicit statement that the trade was NOT under a plan.
_NO_PLAN = re.compile(
    r"\b(?:not|never)\b[^.;|]{0,60}?(?:pursuant to|under|adopt\w*|entered into|enter\w*|"
    r"part of|subject to|covered by|made under|relied? on)[^.;|]{0,30}?10b5-?1"
    r"|\b(?:no|without|neither|absence of)\b[^.;|]{0,30}?10b5-?1"
    r"|\bnot\b[^.;|]{0,20}?10b5-?1",
    re.IGNORECASE,
)
# Any negation word near 10b5-1 that the explicit patterns did not resolve: unknown, not True.
_NEGATION_WORD = re.compile(
    r"\b(?:not|no|never|neither|nor|without|none|n't|cannot)\b", re.IGNORECASE
)
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_ADOPTED = re.compile(
    r"adopted (?:by [\w\s,.]{0,40}?)?(?:on |as of )?"
    rf"(?P<date>{_MONTH}\.? \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    economic_class: str
    basis: str

    @property
    def open_market(self) -> bool:
        return self.economic_class in {"open_market_buy", "open_market_sell"}


def classify(code: str, *, derivative: bool, footnote_text: str | None) -> Classification:
    """Economic class of one reported row, with the rule that produced it."""
    text = footnote_text or ""
    code = code.strip().upper() if code != "INITIAL_HOLDING" else code
    if code == "INITIAL_HOLDING":
        return Classification("holding_snapshot", "holding reported without a transaction")
    mapped = CODE_CLASS.get(code)
    if mapped is None:
        return Classification("other", f"unrecognised transaction code {code!r}")
    if code in {"P", "S"}:
        if derivative:
            return Classification(
                "other", f"code {code} on a derivative: buying or selling a derivative, not stock"
            )
        if code == "S" and _SELL_TO_COVER.search(text):
            return Classification("tax_withholding", "code S with a sell-to-cover / tax footnote")
        if _PRIVATE.search(text):
            return Classification(
                "other", f"code {code} but a footnote indicates a non-open-market transaction"
            )
        return Classification(mapped, f"code {code} with no footnote indicating otherwise")
    if code == "J":
        if _GIFT.search(text):
            return Classification("gift", "code J with a gift footnote")
        if _TAX.search(text):
            return Classification("tax_withholding", "code J with a tax-withholding footnote")
        return Classification("other", "code J without a clarifying footnote")
    return Classification(mapped, f"code {code} maps directly to {mapped}")


@dataclass(frozen=True)
class PlanFlag:
    """`plan` is True only when a footnote affirmatively says the trade was under a 10b5-1
    plan. A filing-wide check box is `possible`: it says some transaction in the filing was,
    not which one, so it never sets a per-row True."""

    plan: bool | None
    basis: str | None  # form_checkbox | footnote
    adopted: dt.date | None
    possible: bool = False


_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


def _parse_date(raw: str) -> dt.date | None:
    cleaned = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"^([A-Za-z]+)\.", r"\1", cleaned).title()
    cleaned = re.sub(r"^Sept\b", "Sep", cleaned)
    for fmt in _FORMATS:
        try:
            return dt.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def plan_flag(footnote_text: str | None, form_checkbox: bool | None) -> PlanFlag:
    """The Rule 10b5-1 status the form itself states, never inferred from the trade.

    A footnote naming a 10b5-1 plan is row-specific; the check box is filing-wide. A negated
    mention ("not adopted a Rule 10b5-1 plan") is False, an unresolved or conflicting mention
    is unknown, never True. A ticked filing check box yields plan=None with `possible`: it
    cannot say which row was planned. Where nothing speaks the answer is None (unknown)."""
    text = footnote_text or ""
    if _PLAN.search(text):
        verdicts: list[bool | None] = []
        adopted = None
        # "Mar. 1, 2026" must not be split into two sentences at the abbreviation period.
        unabbreviated = re.sub(rf"\b({_MONTH})\.(?=\s+\d)", r"\1", text, flags=re.IGNORECASE)
        for sentence in re.split(r"(?<=[.;|])\s+", unabbreviated):
            if not _PLAN.search(sentence):
                continue
            if _NO_PLAN.search(sentence):
                verdicts.append(False)
            elif _NEGATION_WORD.search(sentence):
                verdicts.append(None)
            else:
                verdicts.append(True)
                if adopted is None:
                    match = _ADOPTED.search(sentence)
                    if match:
                        adopted = _parse_date(match.group("date"))
        if verdicts and all(v is True for v in verdicts):
            return PlanFlag(True, "footnote", adopted)
        if verdicts and all(v is False for v in verdicts):
            return PlanFlag(False, "footnote", None)
        return PlanFlag(None, "footnote", None)
    if form_checkbox is True:
        return PlanFlag(None, "form_checkbox", None, possible=True)
    if form_checkbox is False:
        return PlanFlag(False, "form_checkbox", None)
    return PlanFlag(None, None, None)
