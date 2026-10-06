"""Figures for a 10-Q / 10-K document straight from the filing's XBRL, without a model call.

A results press release is extracted by the model: its XBRL does not exist yet. When the
document is a periodic report that is already on EDGAR, the company-facts feed holds every
figure the filing tagged, tied to the filing's accession number. This module turns the
standard metrics of that accession into `ExtractedFigure`s and finds, in the document text,
the line that prints each number, so the validator checks them exactly like model output
(the snippet must be verbatim in the document and contain the number). A figure whose number
cannot be found printed in the document is not produced; the model extraction then runs.
"""

import re
from decimal import Decimal
from typing import Any

from steward.earnings.schema import ExtractedFigure
from steward.earnings.units import SCALE_FACTOR

ACCESSION_IN_URL = re.compile(r"/Archives/edgar/data/(\d+)/(\d{10})(\d{2})(\d{6})/", re.I)
PERIODIC = {"10-Q", "10-K"}
LABELS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "net sales", "sales"),
    "gross_profit": ("gross profit", "gross margin"),
    "operating_income": ("operating income", "income from operations", "operating profit"),
    "net_income": ("net income", "net earnings", "net loss"),
    "eps_diluted": ("diluted",),
}
SCALE_PHRASE = re.compile(r"in (thousands|millions|billions)\b", re.I)
# Quarter or year the 10-Q / 10-K reports in full (not year-to-date).
DURATIONS = {"Q1": (80, 100), "Q2": (80, 100), "Q3": (80, 100), "FY": (350, 380)}


def filing_of(url: str) -> tuple[str, str] | None:
    """(company CIK, accession number "0000320193-26-000006") of an EDGAR filing document URL
    (the accession's first part is the filer agent, not necessarily the company)."""
    found = ACCESSION_IN_URL.search(url)
    if found is None:
        return None
    cik, *parts = found.groups()
    return cik.lstrip("0"), "-".join(parts)


def document_scale(text: str) -> str:
    found = SCALE_PHRASE.search(text)
    return found.group(1).lower() if found else "units"


def _printed(value: Decimal, scale: str, per_share: bool) -> list[str]:
    """How the number would be printed in a table at this scale."""
    scaled = value / SCALE_FACTOR[scale]
    if per_share:
        return [f"{scaled:.2f}"]
    whole = f"{abs(scaled):,.0f}"
    return [whole, f"{abs(scaled):,.1f}"] if scale != "units" else [whole]


def _find_line(text: str, metric: str, numbers: list[str]) -> str | None:
    labels = LABELS[metric]
    for line in text.splitlines():
        lowered = line.lower()
        if not any(label in lowered for label in labels):
            continue
        for number in numbers:
            if re.search(rf"(?<![\d,.]){re.escape(number)}(?![\d,]|\.\d)", line):
                return line.strip()
    return None


def figures_from_facts(
    company_facts: dict[str, Any], accession: str, text: str, requested: set[str]
) -> list[ExtractedFigure]:
    # reconcile imports the check module, which imports this one.
    from steward.earnings.reconcile import CONCEPTS

    scale = document_scale(text)
    unit_line = next((m.group(0) for m in [SCALE_PHRASE.search(text)] if m), None)
    figures: list[ExtractedFigure] = []
    for metric in sorted(requested & CONCEPTS.keys()):
        for taxonomy, concept in CONCEPTS[metric]:
            units = company_facts.get("facts", {}).get(taxonomy, {}).get(concept, {}).get("units")
            figure = _figure(units or {}, metric, accession, text, scale, unit_line)
            if figure is not None:
                figures.append(figure)
                break
    return figures


def _figure(
    units: dict[str, list[dict[str, Any]]],
    metric: str,
    accession: str,
    text: str,
    scale: str,
    unit_line: str | None,
) -> ExtractedFigure | None:
    per_share = metric == "eps_diluted"
    for unit_key, rows in units.items():
        if per_share != unit_key.endswith("/shares") or not (per_share or len(unit_key) == 3):
            continue
        currency = unit_key.split("/")[0]
        # The newest period this filing reports in full: the quarter in a 10-Q, the year in
        # a 10-K (year-to-date columns are skipped; they print the same labels).
        rows = [
            r
            for r in rows
            if r.get("accn") == accession and r.get("form") in PERIODIC and "start" in r
        ]
        for row in sorted(rows, key=lambda r: r["end"], reverse=True):
            fp = row.get("fp")
            days = _days(row["start"], row["end"])
            low_high = DURATIONS.get(str(fp))
            if low_high is None or not low_high[0] <= days <= low_high[1]:
                continue
            value = Decimal(str(row["val"]))
            use_scale = "units" if per_share else scale
            numbers = _printed(value, use_scale, per_share)
            line = _find_line(text, metric, numbers)
            if line is None:
                continue
            written = numbers[0] if per_share or len(numbers) == 1 else _which(line, numbers)
            # The value is what the document prints (the validator compares the two).
            printed = Decimal(written.replace(",", ""))
            signed = -printed if value < 0 else printed
            return ExtractedFigure(
                metric=metric,
                basis="reported",
                fiscal_year=int(row["fy"]),
                fiscal_period=fp,
                period_end=row["end"],
                value_as_written=f"({written})" if value < 0 else written,
                value=float(signed),
                scale=use_scale,
                unit="per_share" if per_share else "currency",
                currency=currency,
                source_snippet=line,
                unit_snippet=None if per_share or unit_line is None else unit_line,
            )
    return None


def _days(start: str, end: str) -> int:
    import datetime as dt

    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


def _which(line: str, numbers: list[str]) -> str:
    return next(n for n in numbers if n in line)
