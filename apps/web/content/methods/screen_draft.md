# Screen draft

The screen draft turns a plain-English request into a list of screener filters that the owner reviews and runs; nothing is run by the parser.

## What it shows

A draft with markets, industries, filters on catalogue metrics, an optional sort, keywords, and the phrases that could not be mapped. A request for a prediction is refused with a reason. The result states whether the parser's evaluation passed for the current metric registry.

## Inputs

- The request sentence (whitespace collapsed).
- The metric registry: each metric's id, label, kind, unit, usual range and aliases.
- The sectors and industries stored in the research data (up to 300 names) and the countries in the universe.
- A configured language model. The model only proposes a draft.

## Method

**Step 1: propose.** The model receives the registry, the stored sectors and countries and the request. It returns a structured draft: filters (metric id, operator, number or list of values, the words it came from), markets, industries, keywords, a sort, unmatched phrases, and a refusal flag. The instructions fix these rules:

- Use only registry metric ids. Operators are `gte`, `lte` and, for category metrics only, `in`.
- Fractions are decimals (4 % is 0.04). Money is in the listing currency. Multiples, counts and years are plain numbers.
- Vague words map to fixed thresholds, for example "low debt" is net debt to equity at most 0.5, "high yield" is dividend yield at least 0.04, "profitable" is net margin at least 0, "large cap" is market cap at least 10,000,000,000 and "small cap" is at most 2,000,000,000.
- Countries are ISO alpha-2 codes. Sectors must use a stored name exactly.
- A sort only when the request asks for an order.
- Forecasts of price or return, and advice, are refused. A partly mappable request is not refused.

**Step 2: validate.** Every proposed item is checked against the registry. Anything that fails is reported back as an unmatched phrase, never guessed:

- Unknown metric id: unmatched.
- Country or sector filters fold into markets (upper-cased, two letters) and industries (matched to a stored name, ignoring case).
- A category metric needs `in` and at least one value.
- A numeric metric needs a number and `gte` or `lte`.
- For a fraction metric whose usual range ends at 1 or below, a value above 1 in absolute terms is read as a percentage and divided by 100, with a note ("read 4 as 4 %"). A value of exactly 1 is not shrunk.
- A value outside the plausible range is unmatched. Plausible means between the first tick minus a span and the last tick plus twice the span, where the span is the tick range (at least 1, or 0.5 for fractions).
- A second limit with the same metric and operator is unmatched.
- A sort must be on a numeric metric.
- At most 12 filters are kept.

**Step 3: cache and log.** The result is cached by request for 30 days. Each request is logged with the draft, the unmatched phrases, the model, the method version and the registry hash. The call runs as a short `screen_parse` job so budget caps apply.

**Evaluation.** `apps/backend/evals/screen_parse_cases.json` holds 40 cases, each a sentence with the expected filters, markets, industries, sort, refusal flag and phrases that must be reported as unmatched. A case scores as exact when its draft has no problems: no missing, unexpected or wrong filters, the right markets, industries and sort, and the expected unmatched phrases. A refusal case is exact when it is refused and has no filters. Filter values match within 0.01 % (relative to the larger of 1 and the expected value); lists match regardless of order. A fraction filter that is off by about 100 times counts as a unit error.

```
exact rate    = exact cases / all cases
unit rate     = cases without a unit error / all cases
refusal rate  = refusals handled correctly / refusal cases
passed        = exact >= 0.85 and units >= 0.95 and refusals >= 1.0
```

A passing run is recorded with the registry hash in `apps/backend/evals/screen_parse_gate.json`. When the registry changes, its hash changes and the recorded pass is reported as out of date.

## Thresholds and defaults

| Setting | Value |
| --- | --- |
| Exact match rate to pass | 0.85 |
| Unit correctness rate to pass | 0.95 |
| Refusal rate to pass | 1.0 |
| Evaluation cases | 40, of which 3 are refusals |
| Maximum filters kept | 12 |
| Model output limit | 1500 tokens |
| Cache time | 30 days |
| Value tolerance in evaluation | 0.01 % |

## When it cannot be computed

- No model is configured, or the model call fails: the request returns an error stating which.
- Another request is being read: try again.
- A budget cap stops the call: the request is refused with the cap message.
- The model returns no usable draft: error.
- A request for a prediction or something no metric measures is refused with a reason and no filters.
- The evaluation line reads "The parser has not been evaluated yet" when no gate file exists, and names an earlier registry when the recorded hash differs.

## Limits

- The model can map a phrase to the wrong metric. The registry check catches unknown metrics, units and implausible values, not wrong choices. Check every filter before running.
- Keywords are shown, not applied as a filter.
- Vague words map to fixed thresholds that may not be what the owner means.
- The evaluation covers 40 sentences. A pass is evidence, not a guarantee.
- At the time of writing the gate file `apps/backend/evals/screen_parse_gate.json` is not present in the repository, so no recorded pass exists.

## Golden examples

The evaluation cases in `apps/backend/evals/screen_parse_cases.json` are the hand-written expectations. Test file: `apps/backend/tests/test_screen_parse.py`.

- `test_acceptance_sentence_becomes_visible_filters`: the filters dividend yield at least 4, net debt to equity at most 0.5 and forward EPS growth at least 0 become 0.04, 0.5 and 0. The yield carries a note that 4 was read as 4 %. The market "us" becomes `US`.
- `test_unknown_or_implausible_filters_are_reported_not_guessed`: an unknown metric, a P/E of 99999, a list filter on a numeric metric, a second P/E limit and an unknown industry are unmatched. One filter, P/E at most 15, survives.
- `test_category_filters_fold_into_markets_and_industries`: country filters "gb" and "DE" become markets `DE`, `GB`, and sector "utilities" becomes "Utilities".
- `test_full_percent_is_not_shrunk`: revenue growth at least 1 stays 1.
- `test_refusal_carries_no_filters`: a refused draft keeps no filters.

The case `acc-01` in the cases file expects "US dividend payers yielding over 4% with low debt and positive earnings growth" to give market US, dividend yield at least 0.04, net debt to equity at most 0.5 and forward EPS growth at least 0.

## Version

Method `screen_parse` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`). The screen itself is method `screen` version 2.1.0.
