"""Fund facts read from a KID: only what the document says word for word is stored."""

import uuid
from typing import Any, cast

import asyncpg
import httpx
import pytest

from steward.config.jobs import AutopilotSettings, FundDocument
from steward.fundresearch import kid_facts as kf
from steward.fundresearch.issuer_sync import IssuerReadError
from steward.llm.routing import RoutingClient
from tests.conftest import MigratedDatabase

ISIN = "IE00B3RBWM25"
URL = "https://example.com/kid.pdf"
TEXT = (
    "Key Information Document. Vanguard FTSE All-World UCITS ETF IE00B3RBWM25.\n"
    "The fund uses a sampling\nmethod to track the index. It was launched on 22 July 2019.\n"
    "Domicile: Ireland. The fund does not lend its securities."
)


def fact(key: str, value: str, passage: str) -> kf.ReadFact:
    return kf.ReadFact.model_validate({"key": key, "value": value, "passage": passage})


def doc() -> FundDocument:
    return FundDocument.model_validate({"isin": ISIN, "url": URL})


def test_clean_value_keeps_only_allowed_forms() -> None:
    assert kf.clean_value("replication_method", " Physical_Sampled ") == "physical_sampled"
    assert kf.clean_value("replication_method", "sampling") is None
    assert kf.clean_value("inception_date", "2019-07-22") == "2019-07-22"
    assert kf.clean_value("inception_date", "22 July 2019") is None
    assert kf.clean_value("domicile", "Ireland") == "Ireland"
    assert kf.clean_value("domicile", "x" * 200) is None


def test_a_fact_must_quote_the_document_across_line_breaks() -> None:
    ok, dropped = kf.verified(
        [
            fact("replication_method", "physical_sampled", "uses a sampling method to track"),
            fact("domicile", "Ireland", "Domicile: Ireland"),
            fact("securities_lending", "no", "never lends securities"),  # not in the text
            fact("ucits", "maybe", "Key Information Document"),  # not an allowed form
        ],
        TEXT,
    )
    assert sorted(ok) == ["domicile", "replication_method"]
    assert dropped["securities_lending"] == "the quoted passage is not in the document"
    assert "not an allowed form" in dropped["ucits"]


def test_html_is_stripped_to_text() -> None:
    page = b"<html><style>p{}</style><p>Domicile:&nbsp;Ireland</p></html>"
    text = kf.document_text(page, "text/html")
    assert "Domicile" in text and "Ireland" in text and "p{}" not in text


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content, self.status_code = content, status
        self.headers = {"content-type": "text/html"}


async def test_a_document_that_does_not_name_the_fund_is_refused() -> None:
    class Http:
        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse(b"<p>Some other fund IE0000000000</p>")

    with pytest.raises(IssuerReadError, match="does not mention"):
        await kf.fetch_document(cast(httpx.AsyncClient, Http()), doc())


@pytest.mark.db
async def test_a_read_document_is_stored_once_and_skipped_until_it_changes(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        owner = await admin.fetchval(
            "insert into auth.users (id, email) values (gen_random_uuid(), 'o@example.com')"
            " returning id"
        )
    finally:
        await admin.close()
    page = {"text": TEXT}
    reads: list[int] = []

    class Http:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        async def __aenter__(self) -> "Http":
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse(page["text"].encode())

    async def model(*_a: Any) -> list[kf.ReadFact]:
        reads.append(1)
        return [fact("domicile", "Ireland", "Domicile: Ireland")]

    monkeypatch.setattr(httpx, "AsyncClient", Http)
    monkeypatch.setattr(kf, "read_facts", model)
    client = cast(RoutingClient, object())

    async def run() -> dict[str, Any]:
        return await kf.read_documents(
            backend_pool, client, AutopilotSettings(), [doc()], uuid.UUID(str(owner))
        )

    assert (await run())[ISIN]["written"] == ["domicile"]
    assert (await run())[ISIN] == {"unchanged": True}
    assert len(reads) == 1

    page["text"] = TEXT + " Updated 2026."  # the document changed; the value did not
    again = (await run())[ISIN]
    assert again["written"] == [] and again["unchanged"] == 1
