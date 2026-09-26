"""The source registry: every record a report may cite, with a stable short id ("S3").

A cited sentence names registry ids only; each entry points at a stored record (a fact, a
news item, a relationship, a 13F filing, a price series, a listing, a verdict, a config
version) or at a web-search result the model saw, with its tier and the stored text that
backs it. The report's source sidebar is this list, and every claim-level citation links
to one of its entries.
"""

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
    as_of: str | None = None


class CitedSentence(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)
    written_by: Literal["code", "model"] = "code"
    check: CheckStatus | None = None
    check_reason: str | None = None


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
