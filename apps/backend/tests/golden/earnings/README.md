# Earnings extraction golden set

Real past quarterly earnings releases, as SEC EDGAR serves them, each with labelled headline
figures. It is meant for evaluating the earnings extraction: run the extractor on
`document.html.gz` and compare what it returns against `figures`.

> **Labels are AI-read and not yet verified.** Claude read every figure in this set from the
> documents and has not hand-checked them against anything except the XBRL crosscheck described
> below. The owner has not checked them either. Before you rely on a case, check its figures
> by hand against the document and change `labelled_by` once you have. Extend the set to the
> companies you actually hold. The current set is a starting sample, not your portfolio.

## Cases

| Company | Ticker | Cases | Source |
|---|---|---|---|
| Apple | `AAPL_US_EQ` | fiscal 2025 Q3 to fiscal 2026 Q3 (5) | 8-K EX-99.1 |
| Microsoft | `MSFT_US_EQ` | fiscal 2025 Q4 to fiscal 2026 Q4 (5) | 8-K EX-99.1 |
| NVIDIA | `NVDA_US_EQ` | fiscal 2026 Q3 to fiscal 2027 Q2 (4) | 8-K EX-99.1 |
| SAP | `SAP_US_EQ` | 2025 Q3 to 2026 Q2 (4) | 6-K EX-99.1 (Quarterly Statement, IFRS, EUR) |
| Novo Nordisk | `NVO_US_EQ` | 2025 Q2 to 2026 Q2 (5) | 6-K primary document (company announcement, IFRS, DKK) |

Novo Nordisk replaces ASML. ASML's 6-K press release (EX-99.1) prints only a small
summary table: this quarter against the previous quarter, basic EPS only, and no operating
income. Its US GAAP statements (EX-99.3) are page images with an overlaid text layer, and
`html_to_text` turns each page of that layer into one very long line. Neither exhibit gives
a usable prior-year quarter or a clean snippet line.

## Layout

```
<case id>/
  document.html.gz   the document bytes exactly as fetched from sec.gov, gzip-compressed
  case.json          metadata and labelled figures
check_cases.py       consistency checker (see below)
```

The fetched bytes include EDGAR's SGML wrapper (`<DOCUMENT><TYPE>EX-99.1 ...<TEXT>`) and a
`<script>` tag that sec.gov appends. `html_to_text` skips the script, so the wrapper header
shows up as the first line of the converted text (for example `EX-99.1 2 foo.htm EX-99.1`).

## `case.json`

- `id`, `ticker`, `company`, `cik` (10 digits), `form` (`8-K` / `6-K`), `source_url`,
  `filing_index_url`, `content_type`, `document`.
- `report_date`: the release date printed in the document. When the filing date differs,
  as with some SAP 6-Ks filed days later, `notes` gives the filing date.
- `fiscal_year_end_month`, `reporting_currency`.
- `period`: `{fiscal_year, fiscal_period, period_end}`, in the company's own fiscal labels.
  For example, Apple's quarter ended June 2025 is fiscal 2025 Q3 and Microsoft's quarter
  ended September 2025 is fiscal 2026 Q1.
- `figures`: a list, one entry per figure:
  - `metric`: `revenue`, `gross_profit`, `operating_income`, `net_income` or `eps_diluted`.
  - `basis`: `reported` (the GAAP/IFRS statement figure) or `adjusted` (the company's own
    non-GAAP/non-IFRS/adjusted figure). `adjusted` is used only for `operating_income` and
    `eps_diluted`, and only where the release clearly prints it for both quarters.
  - `fiscal_year`, `fiscal_period`: every metric has the case's quarter and the same
    quarter of the prior fiscal year, both as printed in this release.
  - `value_as_written`: the number as printed, with no currency symbol or parentheses.
  - `value`: the plain numeric reading in `scale`. It is negative when printed in
    parentheses as a loss.
  - `scale` (`units|thousands|millions|billions`, from the document's unit line), `unit`
    (`currency` or `per_share`; EPS always uses `scale` `units`), `currency` (ISO code).
  - `snippet`: the single line of `steward.documents.text.html_to_text` output that holds
    the figure (table rows are joined with ` | `). It always contains `value_as_written`.
  - `unit_snippet`: for scaled figures whose snippet does not state the scale, the nearest
    line above it that does (usually the table's `(In millions, ...)` header). Added
    automatically from the converted text; check it with the figure.
  - `crosscheck`: set for US cases' reported figures, `null` otherwise. `source: "xbrl"`
    compares against the quarterly (about 90-day) us-gaap fact in SEC companyfacts.
    `source: "xbrl_derived"` is used for fourth quarters, which have no quarterly fact: the
    value is the annual fact minus the nine-month fact. Fourth-quarter diluted EPS can't be
    derived that way, so it has `matches: null` and a `reason`. XBRL values are in raw
    units (dollars, or dollars per share).
- `absent_metrics`: metrics the release genuinely does not present. It is empty for every
  current case.
- `labelled_by`, `notes`: labelling choices per case, such as which line was treated as net
  income or which table a figure came from.

## Labelling choices worth knowing

- Apple and Microsoft call gross profit "Gross margin" in their income statements. That line
  is used for `gross_profit`.
- For SAP, `net_income` is "Profit (loss) after tax", which includes non-controlling
  interests. "Attributable to owners of parent" is printed on the next line. SAP prints
  non-IFRS EPS only as basic EPS, so SAP cases have no adjusted `eps_diluted`.
- In the Novo Nordisk 2025 Q2 and Q3 reports, the income statement's Operating profit and
  NET PROFIT rows contain a stray visible cell (for example `57780000000`). The source HTML
  really prints it, so the snippets keep it.
- Novo Nordisk's adjusted figures follow each release's own definition. The 2026 releases
  also exclude the US 340B provision reversal, so a prior-year adjusted figure can differ
  between releases.

## Checking

From `apps/backend`:

```
uv run python tests/golden/earnings/check_cases.py
```

For every case, the checker converts the document with `html_to_text` and asserts the
following:

- `contains(text, snippet)` is true.
- `value_as_written` appears in the snippet.
- `float(value_as_written without commas) == abs(value)`.
- Figure keys `(metric, basis, fiscal_year, fiscal_period)` are unique.
- Every non-absent metric has reported figures for both the current and the prior-year
  quarter.
- No crosscheck is recorded as a mismatch.
