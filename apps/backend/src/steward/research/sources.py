"""The source registry: every record a report may cite, with a stable short id ("S3").

A cited sentence names registry ids only; each entry points at a stored record (a fact, a
news item, a relationship, a 13F filing, a price series, a listing, a verdict, a config
version) or at a web-search result the model saw, with its tier and the stored text that
backs it. The report's source sidebar is this list, and every claim-level citation links
to one of its entries.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceKind = Literal[
    "fact",
    "news_item",
    "relationship",
    "filing13f",
    "price",
    "listing",
    "verdict",
    "position",
    "strategy",
    "thesis",
    "report",
    "search",
    "observation",
]

CheckStatus = Literal["supported", "unsupported", "unchecked"]


class Source(BaseModel):
    id: str = Field(description="Short id cited by sentences, e.g. S3.")
    kind: SourceKind
    ref: str = Field(description="The stored record: fact id, accession, URL, ...")
    title: str
    url: str | None = Field(default=None, description="External page, when there is one.")
    app_path: str | None = Field(
        default=None, description="The app page for it, e.g. /evidence/12 or /holdings/X."
    )
    tier: int | None = None
    snippet: str | None = Field(default=None, description="The stored text it rests on.")
    as_of: str | None = Field(
        default=None,
        description="The date the record is about (period end, snapshot, bar or publication "
        "date): not when the app learned of it.",
    )
    known_at: str | None = Field(
        default=None,
        description="ISO 8601 time the stored record became known to the app (ingestion time). "
        "For 13F sources it is the filing date (day precision, the only 13F time stored); web "
        "search results carry the retrieval time captured when they were gathered; thesis and "
        "strategy carry the cited config version's created_at. Null for sources without such a "
        "time and for reports that predate the field; never inferred.",
    )


def iso_known_at(value: dt.datetime | dt.date | None) -> str | None:
    """A stored timestamp as an ISO string (UTC for datetimes), None when absent."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC).isoformat()
    return value.isoformat()


def _known_key(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def known_at_range(sources: list[Source], source_ids: list[str]) -> tuple[str | None, str | None]:
    """(oldest, newest) `known_at` over the cited sources; (None, None) when nothing is cited,
    a cited source is unknown or any cited source has no usable known_at."""
    by_id = {s.id: s for s in sources}
    keyed: list[tuple[dt.datetime, str]] = []
    for sid in source_ids:
        source = by_id.get(sid)
        key = _known_key(source.known_at) if source and source.known_at else None
        if source is None or source.known_at is None or key is None:
            return None, None
        keyed.append((key, source.known_at))
    if not keyed:
        return None, None
    return min(keyed)[1], max(keyed)[1]


class CitedSentence(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)
    written_by: Literal["code", "model"] = "code"
    check: CheckStatus | None = None
    check_reason: str | None = None
    known_at_min: str | None = Field(
        default=None,
        description="Oldest known_at over the cited sources (ISO 8601); null when any cited "
        "source has no known_at. Derived, never stored by the writer.",
    )
    known_at_max: str | None = Field(
        default=None,
        description="Newest known_at over the cited sources: the claim as a whole was known "
        "to the app from this time on. Null when any cited source has no known_at.",
    )


class Registry:
    def __init__(self, sources: list[Source] | None = None) -> None:
        self.sources: list[Source] = list(sources or [])
        self._by_key = {(s.kind, s.ref): s.id for s in self.sources}

    def add(self, kind: SourceKind, ref: str | int, title: str, **fields: Any) -> str:
        key = (kind, str(ref))
        if key in self._by_key:
            return self._by_key[key]
        source = Source(
            id=f"S{len(self.sources) + 1}", kind=kind, ref=str(ref), title=title, **fields
        )
        self.sources.append(source)
        self._by_key[key] = source.id
        return source.id

    def get(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def ids(self) -> set[str]:
        return {s.id for s in self.sources}

    def evidence(self, source_ids: list[str]) -> tuple[str, ...]:
        """The stored text behind these sources, for the claim verifier."""
        out = []
        for sid in source_ids:
            s = self.get(sid)
            if s is not None:
                text = " ".join(p for p in (s.title, s.snippet) if p)
                if text:
                    out.append(text)
        return tuple(out)

    def dump(self) -> list[dict[str, Any]]:
        return [dict(s.model_dump()) for s in self.sources]

    @classmethod
    def load(cls, data: list[dict[str, Any]]) -> "Registry":
        return cls([Source.model_validate(d) for d in data])


def sec_filing_url(accession: str, cik: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
