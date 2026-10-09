# Filed statements, peer ratios, IR releases

How filed statement facts are stored, reconciled and used (plan D4.6, D4.8, D4.9, D4.10).
Code: `steward/fundamentals/statements.py`, `steward/peer_metrics.py`,
`steward/fundamentals/ir.py`, `steward/fundamentals/fsds.py`,
`steward/sources/xbrl_concepts.py`.

## The statement fact store (`financial_statement_fact`)

One row per fact of a filing. Sources: `sec_xbrl` (the weekly `sec_xbrl` job reads each
holding's latest 10-K / 10-Q / 20-F instance), `esef` (`valuation_refresh`, filings.xbrl.org),
`sec_fsds` (optional backfill, below), `alpha_vantage` (owner-triggered, unreviewed).

- `statement` and `line_item` come from one catalog (`xbrl_concepts.py`): income statement,
  balance sheet, cash flow, EPS, debt and its maturities, leases, taxes, R&D, repurchases and
  dividends, pensions, segment profit / assets / capex, customer concentration. A fact with any
  dimension is `segment`. `note` holds text blocks (US-GAAP topics: debt, leases, income taxes,
  segments, pensions, equity, revenue, concentration, ...) and ESEF detail facts.
- `fiscal_period`: `FY2025`, `FY2026Q2`, `FY2026H1`, `FY2026M9`. SEC facts are placed on the
  quarter grid from the filing's `dei:DocumentFiscalYearFocus` / `...PeriodFocus` and the
  fact's distance from the document period end; ESEF and backfill facts from their own
  duration and the fiscal-year-end month (the holding profile's, else the one an annual
  report implies, else December and `validation.fiscal_year_end_assumed`).
- `currency` of monetary units, `cik` (text, ten digits), `issuer_id` (confirmed security link,
  else issuer by CIK), `evidence_id` (a cited `source_document_version` of the filing).
  Cover-page facts that are not numbers (`dei:EntityCentralIndexKey`) are in `value_text`.
- Text blocks are capped at 20,000 characters; a cut block carries `validation.text_truncated`
  and `validation.text_length`. An ESEF block the republished xBRL-JSON has no text for is a
  row with `value_text = 'unavailable'` and `validation.text_block = 'unavailable'`.
- `known_at` is the SEC acceptance time (ESEF: the date filings.xbrl.org added the report; if
  none, the moment first stored). It is set once: a re-parse carries the earlier value over.
- Amendments: a 10-K/A replaces the annual report only when its instance carries the
  statements (otherwise the original is parsed). Rows of an amending filing have
  `is_amendment`, `amends_accession`, `revision = original + 1` and `amends_fact_id`; an ESEF
  package re-filed for the same period links the same way.

### Reconciliation (A13)

`validation.reconciled` is `true` (at least one check covered the fact, none failed), `false`
(a check failed) or `null` (nothing checked it). `published` is true only for `true`.
Checks: assets = liabilities and equity; assets = liabilities + equity (with or without the
non-controlling interest); gross profit = revenue - cost of revenue; long-term debt = current
+ non-current; and, for SEC, equality with the SEC companyfacts value of the same accession,
concept, period and unit. Tolerance is the rounding of the values involved plus 0.05% of scale.
Peer metrics never use a failed fact; `reconciled_only=true` also drops unchecked ones, and
every period reports `reconciliation` and `unreconciled_inputs`.

## Peer ratios (`GET /holdings/{ticker}/peer-metrics`)

Facts are grouped by (ticker, basis, period start, period end, unit); balance-sheet facts join
the flow period ending on their date in the same unit. Concept priority per basis is in
`peer_metrics.ALIASES` and the response (`formula_detail`). Cover-page and dimensional facts are
skipped. Every ratio has a guard and an "n/m" reason in `not_meaningful`:

| Metric | Formula | Guard |
| --- | --- | --- |
| gross_margin | gross profit / revenue | revenue > 0 |
| operating_margin | operating income / revenue | revenue > 0 |
| revenue_growth | revenue / prior-year revenue of the same length and unit - 1 | both > 0 |
| net_debt_to_equity | (debt - cash) / equity | equity > 0 |
| free_cash_flow_conversion | (cash from operations - abs(capex)) / net income | net income > 0 |
| roic | operating income x (1 - tax rate) / average of invested capital now and a year earlier; invested capital = equity + non-controlling interest + debt - cash (`ratio_set.nopat_roic`, the one definition used everywhere) | full-year period with a year-earlier full-year period, pretax > 0, tax rate capped to 0-50%, average capital > 0 |

Debt is the total element (`LongTermDebt` / IFRS `Borrowings`), else non-current + current
portion, plus short-term borrowings and commercial paper (US GAAP); leases are excluded.
Peers are compared on the holding's latest period with a metric: same basis, duration within
10 days, end within 45 days. A peer not in a mean is listed with its reason (different
accounting basis, no facts, no comparable period, or the metric's own n/m reason).

## IR releases (`ir_releases` job)

Register a page with `POST /holdings/{ticker}/issuer-ir-sources` (HTTPS, public hosts only).
The job fetches it, hashes it and keeps one `issuer_ir_release` per content hash (a new hash is
a new version with `previous_release_id`; the older document version is marked superseded).
Formats: PDF (pypdf text), XLSX (workbook XML), HTML / text. Recognised layouts: a table with
a header naming metric, period and figures (`Metric | Period | Mean | Low | High | Analysts |
Currency`), and guidance / capital-return sentences ("expects revenue of between $10.5 billion
and $11.0 billion for the fourth quarter of fiscal 2026"). Each figure becomes a
`forward_observation` (provider `issuer_ir`) citing the sentence or row. Consensus without an
analyst count is not stored. `GET /holdings/{ticker}/actual-vs-consensus` compares a reported
period with the last consensus vintage known before the report.

## SEC Financial Statement and Notes backfill (`sec_fsds_backfill`, optional)

Params `{"quarter": "2025q3"}` or `{"url": ...}`, optional `{"ticker"}`. Reads `sub`, `num`,
`tag`, `txt` (`.tsv` or `.txt`) from the zip; keeps standard-taxonomy catalog concepts and the
stored text-block topics of 10-K / 10-Q / 20-F / 40-F filings of holdings with a CIK. A filing
the live job already stored is skipped. The file name pattern and the exact column set of a
current download have not been verified against the live SEC site.
