"""A holding's archived results document as the text source for issuers with no SEC filing.

The earnings check archives every results release it reads (`evidence`, bytes in Storage). For
a holding whose annual report is not on EDGAR (CSG, Chugai, Daiichi) the newest document its
facts were read from is read here, so the same extractors and quote checks apply. The
`accession` of such a text is the document's SHA-256, so each document is read once.
"""

import asyncpg

from steward.exposures.filing_text import FilingText
from steward.fundamentals.ir import IrError, document_lines
from steward.storage import EvidenceStore, StorageError

MIN_CHARS = 2_000


async def archived_text(
    pool: asyncpg.Pool, storage: EvidenceStore, ticker: str
) -> FilingText | str | None:
    """The newest archived document of the holding as text; a reason when it cannot be read;
    None when nothing is archived."""
    row = await pool.fetchrow(
        """select e.sha256, e.storage_path, e.source_url, e.content_type,
                  max(f.period_end) as period_end
           from public.fact f join public.evidence e on e.sha256 = f.evidence_sha256
           where f.ticker = $1
           group by e.sha256, e.storage_path, e.source_url, e.content_type
           order by max(f.period_end) desc nulls last, max(f.extracted_at) desc limit 1""",
        ticker,
    )
    if row is None:
        return None
    try:
        data = await storage.get(row["storage_path"])
        text = "\n".join(document_lines(data, row["content_type"] or ""))
    except (StorageError, IrError) as exc:
        return f"archived document not readable: {str(exc)[:80]}"
    if len(text) < MIN_CHARS:
        return "archived document text too short to read"
    return FilingText(ticker, row["sha256"], "document", row["source_url"], row["period_end"], text)
