# Thesis authoring and discovery

Goal: the app writes the theses itself, tells the owner honestly when a holding should not be
owned, and on request researches what to buy and sell, and writes the thesis for a buy the owner
decides to make. Nothing is traded by the app; the owner decides.

Before this, a research report could only *propose* three text fields (`why_owned`,
`exit_conditions`, `pre_mortem`, `thesis/proposal.py`), the autopilot never wrote `why_owned`,
and KPI thresholds, scenarios, targets and conviction were left to the owner. That rule is
replaced for theses the app authors itself: the owner asked for the app to set all of it, with
the option to conclude that the holding should go.

## Status

| Part | What | State |
|---|---|---|
| A | Full thesis of any holding (core included), manually triggered (`POST /jobs/thesis`, page `/thesis`), autonomous once started, deep research with all app data plus web search | built, unit-tested; not yet run live |
| B | Owner-triggered review: what to sell/trim and what to buy, from deep research (`POST /jobs/portfolio-review`) | built, unit-tested; not yet run live |
| C | Accepting a buy memo writes its thesis (`POST /thesis/memos/{id}/accept`) | built, unit-tested; not yet run live |

Migration `20261112100000_thesis_memo_and_portfolio_review.sql` must be applied before any run. The chat can read memos and reviews (`thesis_memos`, `portfolio_review` tools).

## A. Authoring the thesis of held positions (built)

- `thesis/author.py`: `ThesisDraft` (the model's structured answer: stance keep / add / trim /
  exit, why owned or why not, KPIs, exit conditions, pre-mortem, scenarios, sell and buy target,
  conviction, review interval), `build_thesis` (the code fence), the prompt and the report and
  strategy text it reads.
- The fence: KPI metrics must be ones the earnings check can extract or compute
  (`allowed_metrics()`), units must parse, scenarios must rise bear < base < bull with
  probabilities adding to 1, buy target below sell target, scenario values within 20x of the
  position's price (catches pence-vs-pounds and wrong currency). A refused draft saves nothing.
- `autopilot/thesis_author.py`: `author_ticker` and `author_pending` read the newest deep-thesis
  report (`deep-thesis` or `deep-agentic`, at most `author_report_max_days` old), the strategy and
  the position, and save one `theses` config version noted "AI-authored ... stance X: reason".
  Roll back to revert. A trim or exit stance opens a "Needs you" notice (`thesis_stance`).
- Autopilot step `author_theses` runs before `thesis_drafts`. Settings in `autopilot_settings`:
  `author_theses` (on), `author_model`, `author_effort`, `author_max_per_run` (3),
  `author_report_max_days` (90).
- Core holdings (strategy bucket risk = core) are skipped, as in the draft queue.

Known limits:

- It needs a deep-thesis report first. `draft_theses` queues one research run per autopilot run
  (`draft_max_per_run` 1), so a large backlog fills slowly; raise it, or trigger research per
  holding by hand.
- The numbers rest on the report's computed valuation figures. Where the report has none, the
  model's scenario values are its own estimate; the fence only checks consistency and scale.
- The earnings check can only grade KPIs it can extract. KPIs outside that list are refused, so
  a thesis never contains a KPI that can never be checked.

## B. Owner-triggered portfolio review: what to sell, trim and buy (planned)

A button on the Needs-you page and a chat tool start a `portfolio_review` job. It never trades.

1. **Holdings pass (code + existing theses).** For each holding: thesis stance (A), KPI
   verdicts, price against targets, weight against target band, risk contribution. Anything with
   an exit or trim stance, a breached KPI, or a weight above its hard limit becomes a sell/trim
   candidate with the evidence listed.
2. **Gaps pass (code).** Underweight buckets against `target_categories`, unused risk budget,
   cash above target: where the strategy says money should go.
3. **Candidate pass (code, then cheap model).** Screen a universe (watchlist, peers of holdings,
   the stored fundamentals) for the gaps, rank by the existing quant fields, keep K (default 5).
4. **Deep research on the K candidates** with the `deep-thesis` profile (the cost levers D2-D8
   apply: about $0.2-0.4 each), then the authoring call in not-held mode (`weight=None`).
5. **Pre-trade checks (existing `pretrade` code):** return hurdle, asymmetry gate, start size by
   tier, switch safety margin against the position it would replace, tax and trade cost.
6. **Output:** one report listing *sell/trim*, *buy* (candidate, why, size, what it replaces,
   pre-trade result) and *pass* (researched, rejected, and why), each line cited. Ranked by
   expected return per unit of risk.

Spend control: a per-review cap (default $2) in the job settings; the review stops before the
cap and says what it did not cover.

## C. A buy decision writes its thesis (planned)

- "Buy" on a candidate in the B report calls `author_ticker` for a not-held ticker (the
  position lookup falls back to the stored price) and saves the thesis with the buy case, the
  KPIs that would prove it and the exit conditions, marked "AI-authored (buy)".
- It also opens the existing pre-trade check pre-filled with the suggested size.
- The thesis exists before the order, so earnings verdicts, news links and target alerts work
  from the first day; if the owner then does not buy, the thesis is removed by rolling back.

## Order of work

1. A live run on one holding (about $0.05 for the authoring call once a fresh deep-thesis report
   exists; the research itself costs $0.2-0.6), then read the result critically before enabling
   for all holdings.
2. B holdings and gaps passes in code (no model), then the candidate and research passes.
3. C once B produces recommendations worth acting on.
