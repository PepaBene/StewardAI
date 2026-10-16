# UX / UI visual specification

Date: 2026-10-16. Companion to `ux_ui_final_redesign_plan.md` (the plan says *what* goes where and in which order; this file says *exactly how it looks*). Where the two disagree, this file wins on visual detail and the plan wins on scope and order.

Contents
- **A. Design tokens** — colour (light and dark, validated), type, spacing, grid, radius, motion, number formats.
- **B. Component and chart specs** — 44 pieces, each with use, data, anatomy and sizes, encoding, labels and sort, hover, minimum data, mobile form and table twin.
- **C. Page specs** — every page and tab: question, answer strip with source fields, hero, panels in order, tables and presets, states, desktop and mobile layout.
- **D. Formulas, thresholds and sentence templates** — every band, cutoff and frontend-written sentence the pages use.

How to read a spec line: sizes are CSS px at 1× ; "≥ md" means viewport ≥ 768 px; token names are written without `--` and map 1:1 to `apps/web/src/app/tokens.css`.

---

## A. Design tokens

### A.1 Surfaces and text

| Token | Dark (default) | Light | Use |
|---|---|---|---|
| `page` | `#15181e` | `#f4f5f7` | page background |
| `surface` | `#1d2129` | `#ffffff` | cards, drawers, menus (chart surface; the validator ran against this) |
| `surface-muted` | `#252a33` | `#f0f2f5` | table header, hover row, input background, inactive chip |
| `surface-raised` | `#2e343e` | `#e7eaef` | active chip, selected row, pressed button, tooltip |
| `border-soft` | `rgba(255,255,255,0.06)` | `rgba(20,23,28,0.08)` | table row dividers only |
| `border` | `rgba(255,255,255,0.12)` | `rgba(20,23,28,0.14)` | inputs, focus-less controls |
| `text` | `#eceef2` | `#14171c` | primary |
| `text-muted` | `#a9b0bc` | `#4f5764` | secondary, captions, axis titles |
| `text-faint` | `#6f7785` | `#8a919c` | tick labels, footers, disabled |
| `focus` | `#7aa5ff` 2 px ring, 2 px offset | `#2f6bff` | every focusable element |

Cards have no outer border and no shadow; separation is `surface` on `page` plus 16-24 px spacing. Dividers appear only between table rows and list rows.

### A.2 Data and meaning colours

Validated with the dataviz skill's `validate_palette.js` (2026-10-16). Rule used: every pair of roles that **appears together on one chart** must pass the normal-vision floor (ΔE ≥ 15) against the chart surface; where colour-vision-deficiency separation is in the 6-8 floor band the role always carries a secondary encoding (listed in the last column), which the validator's rules permit.

| Role | Token | Dark | Light | Secondary encoding (mandatory) |
|---|---|---|---|---|
| You / selection / brand / links | `accent` | `#3d7bff` | `#2f6bff` | solid line, filled marks |
| Accent wash | `accent-soft` | `rgba(61,123,255,0.14)` | `rgba(47,107,255,0.10)` | area fill under the portfolio line |
| Gain / good / pass / cheaper | `gain` | `#34c38f` | `#0f8a63` | sign `+`, ▲ arrow, ✓ icon |
| Loss / bad / fail / dearer | `loss` | `#ff6b4a` | `#d4411f` | sign `−`, ▼ arrow, ✕ icon |
| Gain / loss washes (zones, chips) | `gain-subtle`, `loss-subtle` | 14% alpha of the hue | 10% alpha | zone label text |
| Forecast / estimate / projected | `chart-forecast` | `#b968d0` | `#8a3fd1` | **dashed line (4 3) or 45° hatch on fills**, "Estimated" in legend and tooltip |
| Fair value | `chart-fair-value` | `#f39bc8` | `#c4278f` | **stepped line**, "Fair value" label at line end |
| Benchmark | `chart-benchmark` | `#8b93a1` | `#6b7280` | dashed line (6 4), name label at line end |
| Revenue | `chart-revenue` | `#4aa3ff` | `#2577d9` | direct label |
| Earnings | `chart-earnings` | `#34c9b8` | `#0f9c8c` | direct label |
| Warning / limited / stale | `warning-signal` | `#e8b04a` | `#a86f00` | always a text chip ("Limited data", "Stale") |
| Unknown / unmapped | `unknown` | `#3a404b` + hatch | `#d5d9e0` + hatch | "Unknown" label; never drawn as zero |
| Neutral bar / track | `chart-track` | `#2a2f38` | `#e9ecf0` | — |
| Gridline (hairline, only where unavoidable) | `chart-grid` | `#2a2f38` | `#e9ecf0` | — |

Validation record (all-pairs, surface as above):
- dark `#3d7bff, #b968d0, #f39bc8` (you / forecast / fair value): normal ΔE ≥ 16.8, protan 7.1 (floor band → forecast dashed/hatched, fair value stepped) — PASS.
- dark `#f39bc8, #ff6b4a` (fair value over loss zone): normal 15.9, deutan 15.1 — PASS.
- light `#2f6bff, #8a3fd1, #c4278f`: normal ≥ 15.6 — PASS. light `#c4278f, #d4411f`: normal 16.7 — PASS.
- gain/loss is a diverging polarity pair, not categorical; red-green CVD separation is weak by nature, so sign, arrow or icon is mandatory on every gain/loss value and mark (never colour alone).
- revenue / earnings / forecast in light fail CVD (deutan 2.9) between revenue and forecast → forecast is always dashed or hatched, and the past/forecast split carries a "Forecast" divider label.
- warning vs loss in light fail CVD → warning only ever appears as a labelled chip, never as a mark colour next to loss.
- Re-run on every token change: `node <dataviz>/scripts/validate_palette.js "<hexes>" --mode dark --surface "#1d2129" --pairs all` (and light with `#ffffff`); record the output in the R1a PR.

**Diverging scale** (contribution heat, correlation, treemap day move): `loss` (−max) → `loss-subtle` → `diverging-neutral` (`#3a404b` dark / `#e3e6eb` light) at 0 → `gain-subtle` → `gain` (+max). Five steps, clamp at ±3% for day moves, ±1 for correlations. Correlation uses a different pole pair so it never reads as good/bad: `accent` (−1, move oppositely) → neutral → `warning-signal` hue (#e8b04a / #a86f00) (+1, move together).

**Sequential scale** (one hue, light→dark): `accent-ramp-1…6` existing tokens, re-stepped from `#dbe6ff` to `#1d47b8` (light) and `#1c2a4d` to `#9cbcff` (dark).

**Risk scale** (risk score bar only): `risk-low` `#3d7bff` → `risk-mid` `#b968d0` → `risk-high` `#ff6b4a` (dark); `#2f6bff` → `#8a3fd1` → `#d4411f` (light); a continuous gradient across the bar, band labels in text.

**Score mark fill** (`score-mark-weak/mixed/strong`): dark `#f2875a` / `#e8c34a` / `#34c38f`; light `#d9622f` / `#b88a00` / `#0f8a63`; fill at 70% opacity with a 1.5 px stroke of the same hue at 100%; track rings `chart-track`.

**Categorical order** (only when series are identities with no role, e.g. 13F managers, fund overlap pairs; max 6, then "Other"): `accent`, `chart-earnings`, `#e8b04a`, `chart-fair-value`, `chart-revenue`, `#9aa3b2`. Never gain/loss, never forecast.

**Texture** for CVD, print and forced colours: one 45° hatch (1 px line, 4 px gap) for estimates and unknown; 135° hatch reserved for "not applicable".

### A.3 Typography

Families: **IBM Plex Serif** (display), **IBM Plex Sans** (UI), **IBM Plex Mono** (code, ids in Details, cron). All numbers use `font-variant-numeric: tabular-nums lining-nums`.

| Style | Family / weight | Size / line (desktop) | Mobile | Use |
|---|---|---|---|---|
| `display-hero` | Serif 600 | 44 / 48 | 34 / 38 | one hero number per page (portfolio value, income next 12 m) |
| `h1` | Serif 600 | 32 / 38 | 26 / 32 | page title |
| `kpi` | Serif 600 | 28 / 32 | 24 / 28 | KPI tile figure |
| `h2` | Serif 600 | 20 / 26 | 18 / 24 | card / panel / chart title, section title on entity pages |
| `h3` | Sans 600 | 15 / 22 | 15 / 22 | sub-heading inside a card, group header in tables |
| `feed-headline` | Serif 500 | 17 / 24 | 16 / 22 | feed and inbox card headline |
| `body` | Sans 400 | 14 / 21 | 15 / 22 | paragraphs, verdict lines, check sentences |
| `body-strong` | Sans 600 | 14 / 21 | 15 / 22 | check lead-ins |
| `table` | Sans 400 | 13 / 18 | 14 / 20 | table cells |
| `label` | Sans 500 | 13 / 18 | 13 / 18 | form labels, tab labels, chip text |
| `caption` | Sans 400 | 12 / 16 | 12 / 16 | KPI captions, footers, axis titles |
| `tick` | Sans 400 | 12 / 14 | 11 / 14 | axis tick labels (only place 11 px is allowed, mobile only) |
| `eyebrow` | Sans 500 | 12 / 16, sentence case, `text-muted` | same | area name above h1 |
| `mono` | Mono 400 | 12 / 16 | 12 / 16 | cron, ids in Details, code |

Rules: one `display-hero` per page at most; never bold serif inside tables; no all-caps text anywhere except ticker symbols; maximum line length 72 characters for paragraphs.

### A.4 Spacing, grid, breakpoints, radius, elevation

- Spacing scale (4 px base): `1`=4, `2`=8, `3`=12, `4`=16, `5`=20, `6`=24, `8`=32, `10`=40, `12`=48, `16`=64.
- Card padding: 24 (≥ md), 16 (< md). Gap between cards: 24 (≥ lg), 16 (< lg). Section gap on a page: 40.
- Content width: max 1280 px, centred; side padding 32 (≥ lg), 24 (md), 16 (< md). Entity pages: 264 px sidebar + 24 gap + content.
- Grid: 12 columns. Standard spans: full 12; hero 8 + side 4; halves 6 + 6; thirds 4 + 4 + 4. KPI strip: 4 equal tiles (5 allowed on Holdings).
- Breakpoints: `sm` 480, `md` 768, `lg` 1024, `xl` 1280. Below `md`: one column, KPI tiles 2×2, bottom bar on, tables become card lists.
- Radius: `radius-card` 12, `radius-control` 8 (buttons, inputs, segmented controls), `radius-chip` 999, `radius-mark` 4 (bar ends, see B.0). Charts' plot area is never rounded.
- Elevation: none on cards. Drawer, popover and tooltip: `surface-raised` + shadow; menus (dropdowns) use `surface` + `shadow-popover`, since `surface-raised` read too close to the page in dark mode (decided in R1a). Shadow values: `0 8px 24px rgba(0,0,0,0.35)` (dark) / `0 8px 24px rgba(20,23,28,0.12)` (light). Overlay scrim for drawers on mobile: `rgba(0,0,0,0.5)`.
- Touch targets ≥ 44×44 below `md`; ≥ 32×32 on desktop.

### A.5 Motion
- Durations: 120 ms (hover, chip), 200 ms (drawer, tab underline, accordion), 400 ms (chart enter, one time only), 600 ms (number count-up on first paint only).
- Easing: `cubic-bezier(0.2, 0, 0, 1)` for enter, `cubic-bezier(0.4, 0, 1, 1)` for exit.
- `prefers-reduced-motion`: all of the above become 0 ms except opacity fades (100 ms).
- Skeletons: `surface-muted` blocks in the final shape, a 1.2 s shimmer, no spinners in content areas.

### A.6 Number, date and text formats (one module: `lib/format.ts`)
| Kind | Format | Examples |
|---|---|---|
| Money, hero | currency code + full number, no decimals ≥ 1,000 | `CZK 40,442` |
| Money, tile/axis/table | code + compact ≥ 10,000 | `CZK 40.4k`, `USD 1.2m`, `CZK 980` |
| Money, per share | code + 2 decimals | `USD 342.00` |
| Change in money | sign always | `+CZK 323`, `−CZK 71` (true minus `−`) |
| Percent | 1 decimal; 2 decimals below 1%; sign on changes | `5.8%`, `0.16%`, `+0.84%` |
| Percentage points | `pp` | `−5.0 pp` |
| Multiples | 1 decimal + `×` | `29.3×` |
| Ratios without unit | 2 decimals | `1.23` |
| Scores | `n / 6` with 1 decimal when fractional | `4.5 / 6` |
| Counts | thousands separator | `1,571` |
| Dates | `16 Oct 2026`; in tables `16 Oct`; in axes `Oct` / `Oct 26` | |
| Relative time | `21 min ago`, `3 h ago`, `2 d ago`, then the date | |
| Missing | `—` with a tooltip giving the reason (NullCell) | |
| Instrument | name + symbol chip | `Apple` `AAPL` |
| Estimated value | value + `est.` chip in `chart-forecast` outline | `CZK 22 est.` |

---

## B. Component and chart specs

### B.0 Rules that every chart obeys
- Rendered only inside `ChartFrame` (B1); below its minimum data it renders `LimitedState` (B6) instead.
- Marks: bars ≤ 24 px thick, 4 px rounded data end, square at the baseline, 2 px surface gap between touching bars or stacked segments; lines 2 px, round joins; markers ≥ 8 px with a 2 px surface ring; area fills at 10-14% opacity of the series hue.
- No gridlines by default. Use zones (B.0 zones) and labelled reference lines. If a value axis is needed: 3-5 ticks, `tick` style, `text-faint`, no axis line; a single hairline baseline at zero for bars.
- Reference line: 1 px `text-muted` dashed (3 3) with a label at the right end (`caption`, `text-muted`), e.g. "Portfolio 30.0%", "S&P 500 17.3%", "Peer median 38.7×", "Limit 75%".
- Zone: a background band in `gain-subtle` / `loss-subtle` / `surface-muted` behind the plot, with a zone label top-left inside the band (`caption`, `text-muted`), e.g. "Cheaper than fair value".
- Text never takes a series colour; identity comes from a swatch, line key or the mark next to the text.
- Legend: present for ≥ 2 series, placed under the plot, left aligned, swatches 10×10 (bars) or 16×2 line keys; ≤ 4 series are also direct-labelled at the line end.
- Tooltip (hover on desktop, tap on touch; tap elsewhere to dismiss): `surface-raised`, radius 8, padding 12, max width 280; first line date or category in `label`, then one row per series: swatch, name (`text-muted`), value right-aligned (`table`, tabular); optional last row as a one-line reading in `caption`. Crosshair: 1 px vertical `text-faint` on time series.
- Hit targets: bars the full band height; dots 24 px circle; lines a 12 px invisible stroke.
- Every chart has a table twin built from the same rows (the `Data` toggle), sorted like the chart, with units in headers and the reason column for missing values.
- Animation: one 400 ms enter on first render, none on data refresh.

### B1. ChartFrame
- **Use:** wrapper for every chart.
- **Anatomy:** header row: `h2` title + 16 px `i` icon (popover with a 1-2 sentence definition and "How it's computed →" link to `/methods/<slug>`); right side: range chips (B40) and view toggles; far right `⋯` menu (Download CSV, Copy link, Full screen). Under the title one `body` `text-muted` reading line (optional, from VerdictLine). Plot. Legend. Footer row (`caption`, `text-faint`): `Data` toggle · `Learn` · dataset · "Updated 21 min ago" · timeframe · source; on narrow widths the footer wraps after `Learn`.
- **Sizes:** padding 24 / 16; plot height hero 320 (≥ md) / 220 (< md); secondary 220 / 180; compact 140; spark 36.
- **States:** loading (skeleton of plot height with 3 faint bars or a flat line), limited (B6), error ("Couldn't load · Retry", Details collapsed with the technical text).
- **Full screen:** opens a dialog at 92vw × 86vh with the same chart, legend and table side by side ≥ lg.

### B2. KpiTile and AnswerStrip
- **Use:** every page's answer strip; key figures inside entity sections.
- **Anatomy (tile):** caption label (`caption`, `text-muted`, one line); figure (`kpi`, `text`); change line under it (`caption`; ▲/▼ + signed value in `gain`/`loss` + period, e.g. "▲ +0.84% today"); optional `VerdictLine` (B3) max two lines; optional sparkline right-aligned 96×36 (B10); optional badge (B7) after the caption. Left rule 3 px in `gain`/`loss`/`accent` only when the tile is a signed result (P/L, return), else none.
- **Strip:** 3-5 tiles in one card, separated by 1 px `border-soft` vertical dividers (≥ md) or a 2×2 grid with 1 px dividers (< md). Height 112 desktop (124 with verdict). The first tile may use `display-hero` when the page has no hero chart.
- **Data:** `{label, value, unit, change?, changePeriod?, verdict?, verdictTone?, spark?: number[], badge?}`.
- **Empty value:** `—` with the reason in a tooltip and the verdict replaced by the reason sentence.
- **Table twin:** none (it is text).

### B3. VerdictLine
- **Use:** one sentence beside a number (tiles, check rows, chart reading lines).
- **Anatomy:** 14 px icon (✓ gain, ✕ loss, ! warning, i neutral) + sentence (`body` or `caption` in tiles), figures inside the sentence in `body-strong`; tone from the verdict, never from the sign of the number.
- **Source:** backend `sentence` where provided; otherwise `lib/verdicts/*` templates (D.3).

### B4. ScoreMark
- **Use:** company profile (5 axes Value, Future, Past, Health, Dividend), portfolio aggregate, Discover idea aggregate, fund mark (Cost, Diversification, Tracking, Size, Data quality).
- **Geometry:** 5 axes at angles −90°, −18°, 54°, 126°, 198° (Value at top, clockwise). Radius scale 0-6 mapped to 0.18R … R (a 0 score still shows a small core, so 0 ≠ missing). Track: three concentric rings at 2, 4, 6 in `chart-track`, 1 px. Shape: the five points joined by a closed Catmull-Rom spline (tension 0.5), filled with the score-mark tone at 70% and stroked 1.5 px at 100%.
- **Tone:** mean of known axes: < 2.5 weak, 2.5-4.0 mixed, > 4.0 strong (D.1).
- **Unknown axis:** the spline passes through the 0.18R core on that axis and the axis gets a 2 px dashed `text-faint` notch line from core to the ring with "?" at the ring; tooltip names the missing inputs. Not-applicable axis: same but label "n/a" and no "?".
- **Sizes:** 24 (row avatar: no labels, no rings except outer ring), 64 (header: outer ring only, no labels), 120 (entity sidebar: rings, axis initials V F P H D outside), 240 (scorecard: rings, full axis names on the outside along the curve, clickable axis points 12 px with a 32 px hit target, selected axis point filled `accent`).
- **Interaction (240 only):** click an axis point → selects axis, updates the side panel; keyboard: arrow keys move between axes.
- **Table twin:** axis, score n/6, checks passed/applicable, unknown count.
- **Mobile:** 240 becomes 200; labels shorten to initials under 200.

### B5. CheckList and All-checks dialog
- **Use:** company checks, risk checks, dividend checks, release checks, verification gates, data-quality issues, autonomy targets, strategy compliance, editor validation.
- **Row anatomy:** 16 px state icon (✕ `loss` fail, ✓ `gain` pass, ! `warning-signal` warn, ○ `text-faint` not applicable, ? `text-faint` unknown) · lead-in in `body-strong` · sentence with figures in `body` · right: "See the figures" link (`caption`, opens the evidence drawer). Row padding 10 0, divider `border-soft`.
- **Order:** fail → warn → pass → collapsed group "n couldn't run · missing X, Y" (unknown) → collapsed "n don't apply".
- **Header:** "n of m applicable checks pass" in `caption` + a 6-segment bar (each segment 12×6, 2 px gap, filled `gain` for pass, `loss` for fail, `chart-track` for unknown/n.a.).
- **Dialog:** 720 px wide, sticky header with counts and filter chips (All · Failed · Passed · Couldn't run), rows grouped by axis/section; on mobile a full-screen sheet.
- **Table twin:** check, state, value, threshold, inputs, section.

### B6. LimitedState
- **Use:** any chart or panel below its minimum data (D.2).
- **Anatomy:** inside the ChartFrame in place of the plot, height 56: 16 px `i` icon + sentence "Needs {min} {unit}, has {have}." + "What fills it" link (to the job, editor or data-quality item) + optional "Recording since 16 Oct" when the data is accumulating. Title, `i` and footer stay.
- **Never:** an empty axis, an empty dashed box, a grid of "Unknown".

### B7. Badge / chip
- Pill, radius 999, height 22, padding 0 8, `caption` 500. Variants: `limited` (warning-signal outline + text), `estimated` (chart-forecast outline), `stale` (warning-signal fill 14%), `n/a` (surface-muted), `new` (accent fill, accent-foreground text), `kind` chips in feeds (surface-muted + 12 px icon). Status chips always include text, never colour only.

### B8. TimeSeries line chart
- **Use:** portfolio value vs benchmark (Overview hero), price chart (company Overview), value with cost-basis line (Holdings/Performance), core vs stocks, rolling metrics, macro small multiples.
- **Anatomy:** x axis dates (3-6 ticks, `Oct`, `Jan 27`); y axis on the **right**, 3-5 ticks; series: you `accent` 2 px with `accent-soft` area to the baseline; benchmark `chart-benchmark` dashed 6 4; cost basis `text-muted` dashed 2 3 stepped; fair value StepLine (B9); forecast segment `chart-forecast` dashed with a vertical "Forecast" divider (1 px `text-faint`, label at top).
- **End labels:** last value of each series at the right edge in a pill (`surface-raised`, `caption`): "Portfolio 163.2", "S&P 500 111.2".
- **Zones:** optional lead/lag shading between portfolio and benchmark lines (`gain-subtle` where ahead, `loss-subtle` where behind) — on for the Overview hero, off elsewhere.
- **Events:** lollipops on the x axis (6 px dot on a 12 px stem) for earnings, dividends, alerts; tooltip lists them.
- **Controls:** range chips 1M · 3M · YTD · 1Y · 2Y · All (default 1Y); toggle Indexed / Value where both make sense.
- **Hover:** crosshair + tooltip with date, each series value and, for indexed charts, the gap ("ahead by 52.0 points").
- **Minimum:** 5 points in range; else LimitedState.
- **Mobile:** height 220, y ticks 3, end labels collapse into the legend.
- **Table twin:** date, each series, gap.

### B9. StepLine overlay and Check-in sparkline
- **StepLine:** `chart-fair-value` 2 px, stepped `after`, starts at the first stored value (no back-fill), label "Fair value" at the right end; changes of basis are marked by a 6 px open circle with the new basis in the tooltip.
- **Check-in sparkline** (Holdings rows, Watchlist, Valuation table): 120×36 (desktop) / 96×32 (mobile); price as a 1.5 px line in `text-muted` with an area wash coloured by position vs fair value at the last point (`gain-subtle` if below fair value, `loss-subtle` above, none if no fair value); fair value StepLine 1.5 px; a 1 px `text-faint` vertical marker at today; no axes; tooltip on hover with date, price, fair value, gap.

### B10. Sparkline (tiles)
96×36, 1.5 px line in `accent` (or `text-muted` when not "you"), area wash 10%, last point 4 px dot; no axes; no tooltip in tiles (the tile carries the value).

### B11. Candle chart
- Up candles `gain` filled, down candles `loss` outlined with `loss-subtle` fill (shape differs as well as hue); wick 1 px; body max 8 px; volume bars below at 20% of plot height in `chart-track`; MA 20 as 1.5 px `text-muted`; last price pill on the right axis in `accent`.
- Default view on company Overview is the line chart (B8); candles are a toggle.

### B12. Underwater drawdown
- Area from 0 down to the drawdown in `loss` at 18% with a 1.5 px `loss` edge; benchmark drawdown as a dashed `chart-benchmark` line; label at the deepest point ("−17.4% · 3 Aug"); zero line hairline; y ticks 0, −10%, −20% (rounded).
- **Minimum:** 20 observations. **Table twin:** date, drawdown, benchmark drawdown.

### B13. Column chart and ForecastBars
- **Use:** dividends by month/year, net flows, spend by month, filings per quarter.
- **Bars:** ≤ 24 px, 4 px top radius, 2 px gap; history in `accent` (dividend history, spend) or `gain`/`loss` when the value is signed (net flow); estimates in `chart-forecast` with 45° hatch and a vertical "Forecast" divider.
- **Axis:** starts at the first non-zero period, or 12 periods before the first estimate, whichever is earlier; partial periods hatched in `chart-track` with a "partial" tick suffix.
- **Toggle:** Monthly / Annual.
- **Hover panel:** to the right of the chart ≥ lg (fixed panel 240 px), else tooltip: period, total, "n paid / n estimated", then a mini table (symbol chip, pay date, amount, est. chip).
- **Value labels:** only on the largest bar and the latest bar.
- **Minimum:** 3 non-zero periods. **Table twin:** period, received, estimated, items.

### B14. Ranked horizontal bars
- **Use:** contributors, allocation by holding, peer multiples, yield vs peers, per-holding risk, sources volume, cost by job.
- **Anatomy:** label column (EntityName with 20 px avatar where an entity; max 28 characters, ellipsis + tooltip); bar ≤ 16 px high on a `chart-track` full-width track, 4 px right radius; value at the bar tip (`table`, tabular); optional side column (e.g. "growth E 12.4%") right-aligned; reference lines vertical dashed with labels (portfolio, market, peer average, median); the subject row (this company / your portfolio) highlighted with `accent` and a "This company" tag, others `text-muted` at 60%.
- **Sort:** descending by value; max 10 rows, then "n others" row (sum) with expand.
- **Colour:** `accent` for identity measures (weight, value), `gain`/`loss` for signed or verdict measures.
- **Minimum:** 2 rows (peer charts 3 peers). **Mobile:** label above the bar, value at right. **Table twin:** rank, name, value, side column.

### B15. Diverging bars (centred on zero)
- **Use:** contribution to return, active weights vs benchmark, factor loadings, change by sector, insider net buying.
- Zero line 1 px `text-muted`; positive bars to the right in `gain`, negative to the left in `loss` (for active weights and factors, which are not good/bad: `accent` right, `chart-benchmark` left); labels on the outside end; sorted by value (positives top) or by absolute value when the toggle "Largest first" is on; unattributed/residual row last in `unknown` with hatch.
- Max 12 rows + "others". **Table twin:** name, value, share of total.

### B16. Waterfall
- **Use:** returns breakdown (unrealised, realised, dividends, currency, fees → total), attribution, cash → FCF.
- Floating bars ≤ 32 px wide, 2 px gap; positive `gain`, negative `loss`, totals `accent`; connector lines 1 px dashed `text-faint` between bar ends; value above each bar; a tiny value (< 1% of the axis range) draws as a 2 px line plus the label.
- **Minimum:** 2 non-zero components. **Table twin:** component, amount, share of cost.

### B17. Treemap
- **Use:** holdings sized by weight (colour = day move or P/L), sector → holding, 13F positions, fund holdings.
- Squarified, 2 px surface gaps, radius 4 on tiles ≥ 40 px; colour by the diverging scale (A.2) clamped at ±3% (day move) or ±30% (P/L); grey `unknown` hatch for no figure. Labels inside when the tile is ≥ 64×40: symbol (`label` 600) top-left, value bottom-left (`kpi` scaled to tile: 14-28 px); weight top-right in `caption`. Smaller tiles: no label, tooltip only.
- Legend: a 5-step diverging key with end labels "−3%" "+3%".
- **Mobile:** below 480 px switch to ranked bars (B14) with the same colours. **Table twin:** name, weight, value, colour metric.

### B18. Sankey
- **Use:** portfolio → sector → industry → holding; revenue → gross profit → earnings (company Past); supply chain flows; money in → invested / cash.
- Nodes 12 px wide bars in `text-muted`; links in the source node's tone at 30% (for the income statement: revenue `chart-revenue`, costs `loss` at 30%, earnings `chart-earnings`); node labels outside: name (`label`) + amount and share (`caption`); a level toggle (Sector / Industry / Holding) above.
- Income statement version has a **year strip** above: a 48 px-high mini line of revenue by year with clickable year dots (selected year `accent` 8 px), keyboard left/right.
- **Minimum:** 3 links and 2 levels; else ranked bars. Max 30 nodes; beyond that, "Other" per level. **Mobile:** ranked list with "Open diagram" (full-screen landscape). **Table twin:** source, target, value.

### B19. DotStrip
- **Use:** price vs fair value per holding, forecast growth, ROE, debt/equity, yield; one strip per metric; also company valuation "all bases" strip.
- **Anatomy:** 72 px tall strip per metric: metric name (`h3`) + basis tabs (segmented control) above; horizontal axis in the metric's unit with 5 ticks; zones (e.g. price vs fair value: left of 0 `gain-subtle` labelled "Cheaper than fair value", right `loss-subtle` "Dearer"); reference lines for portfolio and benchmark with labels; dots 10 px (or sized by weight 8-20 px, area-proportional) in `accent` with 2 px surface ring; labels: symbol chip next to the dot, collision-avoided in two lanes above and below; if more than 12 dots, label the 6 largest by weight and the 2 extremes, others on hover.
- **Sign convention:** price vs fair value = (price − fair value) / fair value; negative = cheaper = left = gain zone. One helper `priceVsFair()` used by this strip and the Valuation page chip so they can never disagree.
- **Outliers:** axis domain = p2…p98 of values, padded 10%; values outside render as a 6 px chevron at the edge with the value label.
- **Minimum:** 3 placed holdings; not-placed holdings listed once under the strip ("Not placed: 25 · why").
- **Mobile:** strip full width, labels only on hover/tap, list fallback under 360 px. **Table twin:** name, value, weight, zone.

### B20. BandGauge
- **Use:** valuation (three zones), risk score (gradient), budget burn (three zones), concentration.
- **Valuation:** a 16 px tall bar, three zones: `gain-subtle` "20% undervalued", `surface-muted` "About right", `loss-subtle` "20% overvalued" (zone labels under the bar, `caption`); marker: a 2 px `text` vertical line 28 px tall with a label pill above ("Price USD 342 · 40.7% above fair value"); the fair value point labelled under the bar. Scale ±60%, clamp with an arrow beyond.
- **Risk:** continuous `risk-low → risk-mid → risk-high` gradient, labels Low / Moderate / High / Very high under quarter marks, markers for "My portfolio 78" and "S&P 500 46"; score `kpi` at top right "78 / 100".
- **Budget:** zones on track / tight / over at 0-100% / 100-110% / > 110% of cap for projected month-end spend; markers for actual and projected.
- **Without a marker value:** never render; show LimitedState.

### B21. Bullet chart
- **Use:** autonomy targets, KPI vs target (theses), hit rate vs expected, budget per job, drift vs band.
- 12 px bar on a `chart-track` track; qualitative range shading (two lighter steps of the track) when ranges exist; target as a 2 px `text` tick 20 px tall; value bar `accent` (or `gain`/`loss` when the verdict is good/bad); label left, value and "target X" right; verdict icon at the far right.

### B22. HeatMatrix (triangle)
- **Use:** holdings correlation (90-day), fund overlap (fund × fund), rule × source verification.
- Lower triangle only, no diagonal; cells 28-40 px square with 2 px gaps and radius 4; colour: correlation pole pair (A.2) from −1 to +1, neutral at 0; value printed in cells ≥ 32 px (`caption`, text colour chosen for contrast); row and column labels as symbol chips; hover highlights the row and column and shows "AMD and NVDA moved together 0.82 over 90 days".
- Beside it two short lists: "Move most together" (top 5 pairs) and "Move least together" (bottom 5), each row two chips + coefficient.
- **Minimum:** 3×3 and 60 shared trading days per pair (pairs below: hatched cell, "too few days" tooltip); if fewer than 50% of pairs qualify, LimitedState for the whole matrix.
- **Mobile:** pinned first column, horizontal scroll inside the card, plus the two lists first. **Table twin:** pair, coefficient, shared days.

### B23. StatusMatrix
- **Use:** job runs by day, dataset coverage per issuer, release checks per week, source health per day, inbox ageing.
- Rows = items (name + last value at row end), columns = days/periods (max 21 visible, scroll for more), cells 14×14 with 2 px gaps, radius 3: OK `gain`, retried `warning-signal`, failed `loss`, not scheduled `chart-track`, unknown hatch; each state also has a distinct glyph in the tooltip and the legend (✓ ↻ ✕ –).
- Group rows by category with collapsible `h3` headers and counts.
- **Table twin:** item, then one column per day with the state word.

### B24. MonthCalendar
- Seven-column month grid (Monday first, CZ locale), today outlined in `accent`; each event a 6 px dot plus a chip with symbol and short label ("NVDA Pay USD 10.00"); dot styles: ex-dividend filled `accent`, estimated ex-dividend hatched outline `chart-forecast`, payment filled `gain`, estimated payment outline `chart-forecast`, earnings `chart-revenue` square, other `text-muted`; max 3 chips per day then "+n".
- Prev/next month arrows and a "Today" button; legend under the grid. Under `md`: agenda list grouped by day with the same chips; "Open calendar" toggles the grid.
- Events table below: date, entity, event, amount (est. chip).

### B25. Histogram with highlighted bin
- **Use:** industry multiple distribution (company Valuation), monthly returns (Risk › Bad months), dividend yields vs market.
- Columns in `chart-track` (normal) with the company's bin in `accent`; average line dashed `text-muted` labelled; zones left/right of the average (`gain-subtle` / `loss-subtle`) only where lower is better (multiples); the worst-5% tail shaded `loss-subtle` for returns; a bin tab strip under the chart selecting a bin's company table.
- **Minimum:** 20 items. **Table twin:** bin, count, items.

### B26. FanBand (analyst targets)
- Price line (`accent`) for the past 12 months; consensus mean target line (`chart-forecast`, dashed) over the same period; dispersion band (high/low) `chart-forecast` at 12% behind it; a future zone (right 30% of the plot, `surface-muted`) with a cone from the last price to the 12-month high and low (`chart-forecast` at 10%) and a dashed line to the mean target; labels at the cone end: High, Mean, Low with values and % from price.
- Tooltip card: date, analysts n, price, mean target (+%), agreement text.
- **Minimum:** 3 analysts and 4 stored snapshots; else show a single range bar (low–mean–high vs price) with "Recording since …".

### B27. Lollipop timeline
- **Use:** insider trades over price, deals timeline, alerts timeline, escalation history, filings.
- Horizontal time axis; stems 1 px `text-faint`; heads 8-16 px (size = value) coloured by kind (buy `gain`, sell `loss`, other `text-muted`), stacked when same day; optional price line above (`text-muted` 1.5 px).
- **Table twin:** date, kind, entity, value.

### B28. Dumbbell (before → after)
- **Use:** Decide (weights before/after), what-if risk deltas, fund compare.
- Two 10 px dots joined by a 2 px line: before `chart-benchmark`, after `accent`; delta label at the after-dot (`caption`, signed); rows sorted by absolute change; target band shading where a target exists.

### B29. Share bar and donut
- **Share bar** (default for part-to-whole ≤ 6 parts): one 16 px stacked bar, 2 px gaps, categorical order (A.2), labels under each segment ≥ 8% ("Technology 42%"), smaller ones in the legend.
- **Donut** only for a single-entity mix ≤ 7 parts with a centre total (company segments): 160 px, 24 px ring, gaps 2 px.
- More than 6-7 parts → ranked bars or treemap.

### B30. Small multiples
- Grid of compact line charts (140 px plot), shared x range, independent y with 3 ticks and units in each title ("Brent, USD/bbl"); latest value and change at top right; revised periods marked with a hatched band.
- Columns: 3 (≥ lg), 2 (md), 1 (< md).

### B31. Lorenz / concentration curve
- Cumulative weight (y) vs cumulative count share (x) for holdings; equality diagonal dashed `chart-benchmark`; portfolio curve `accent` with area between curve and diagonal at 10%; annotation at 20% of names ("Top 6 names = 51% of value").
- Beside it a BandGauge for effective number of holdings (D.1).

### B32. Network graph
- Force layout ≤ 60 nodes; nodes 8-20 px sized by revenue share or weight, held companies `accent` with ScoreMark 24 avatar on hover; edges 1-3 px, confirmed solid `text-muted`, suggested dashed `text-faint`; edge direction arrow for supplier → customer; labels on held companies and the 8 largest others.
- Default on mobile: ranked list of links with "Open diagram".

### B33. Week timeline (jobs)
- Seven columns (Mon-Sun) × 24 h rows compressed to 4 h bands; each schedule a 6 px dot at its run times, coloured by job group (categorical); hover lists jobs at that time; a now-line in `accent`.
- Above the jobs list in Settings › Jobs and System › Activity.

### B34. Severity matrix (event rules)
- Rows = event types; columns = Ignore · Digest · Highlight · Alert as a segmented control per row (radius 8); selected segment filled with the sequential accent ramp (1, 3, 5, 6) plus the word; per-holding overrides indicated by a small count chip at the row end ("3 overrides").

### B35. Tables: GroupedTable, ResponsiveTable, presets
- **Header:** `surface-muted`, `caption` 600, sentence case, sort arrows on hover; sticky under the page header.
- **Rows:** 52 px (with avatar) / 44 px (plain); dividers `border-soft`; hover `surface-muted`; selected `surface-raised`; numbers right-aligned tabular; first column EntityName (B42).
- **Groups:** group header row with `h3` name, count chip and one-line explanation in `caption` (e.g. "Overvalued 4 · price above the fair value on this basis"); collapsible; groups ordered by importance (action-needed first).
- **Presets:** segmented control above the table (2-5 presets + Custom), right side search and filter button; Custom opens a column picker (checkbox list, drag to reorder).
- **Pagination:** 25 rows per page default, "Show 25 more", virtualised beyond 200.
- **Below `md`:** ResponsiveTable renders CompactRow (B42) cards; sort/filter in a bottom sheet.
- **CSV:** every table has Download CSV in its `⋯` menu.

### B36. FeedCard
- **Use:** news feed, updates, disclosure alerts, AI decisions, activity log entries.
- **Anatomy:** left 32 px EntityName avatar (ScoreMark 24 inside a 32 px circle or logo initials); line 1: entity name (`label`), kind chip (B7), age (`caption`, `text-faint`); right side: symbol, price, day move (▲/▼ coloured) for company items; headline (`feed-headline`, max 2 lines); summary (`body`, `text-muted`, 2 lines + "Show more"); footer: source chips with tier dot, "n similar" link, actions (Useful / Not useful / Mark read).
- Unread: 6 px `accent` dot left of the headline. Grouped items: "12 corporate-structure updates for Alphabet" single row, expand to list.
- Card padding 16, divider between cards (no card per item on desktop lists; cards on mobile).

### B37. InboxItem
- FeedCard anatomy plus: a "why it's here" sentence; evidence links; primary action button (`accent`) + secondary (outline) + Snooze menu (1 day, 1 week, until next earnings) + Dismiss; state chips (Proposed, Waiting, Snoozed until …).
- Batch variant: headline "Write down why you bought 20 holdings", avatar stack of 5 + "+15", primary "Start" (opens a stepper in the drawer), secondary "Dismiss all".
- Ageing: a 3 px left edge `warning-signal` when older than 7 days, `loss` when older than 14 days.

### B38. SideDrawer
- Right panel 560 px (≥ lg), 480 (md), full-screen sheet (< md, slides up); header: EntityName + key figure + close; tabs (B40) under it; content scrolls; footer actions sticky; "Full page →" link in header. Focus trapped; Esc closes; URL gets `?drawer=<id>` so it is linkable.

### B39. EntityLayout (T2)
- **Sidebar** 264 px sticky (top offset = header height + 16): ScoreMark 120 (or status mark), name (`h2`), symbol + exchange chips, key figure (`kpi`) with day change, fair value chip ("Fair value USD 243 · 40.7% above" in `chart-fair-value` outline), primary actions (Ask, Start research), position card if held (value, P/L, weight, small sparkline), then the numbered section list: number (`caption`, `text-faint`), section name (`label`), check count chip ("3/6"); active section `accent` left bar 3 px. Ticker switcher (search field) at the top of the sidebar.
- **Content:** each section starts with `eyebrow` "Section 2" + `h2` name + "n of 6 checks" header (B5) + key figures (KpiTiles) + check rows + charts; data-status footer at the end of each section: table with dataset, last updated, timeframe, source.
- **Mobile:** sidebar collapses to a header card (mark 64 + name + price + fair value chip + actions) and a sticky horizontal section chip row.

### B40. TabBar, segmented control, filter chips, range chips
- **TabBar:** text tabs `label`, 40 px tall, active `text` with a 2 px `accent` underline, inactive `text-muted`; overflow into "More ▾"; no scrollbar visible; on mobile a horizontally scrolling chip row with edge fade, active chip scrolled into view.
- **Segmented control:** `surface-muted` track radius 8, selected segment `surface-raised` with `text`; 32 px tall.
- **Filter chips:** pill 28 px, `surface-muted`, selected `accent` fill; count after the label ("Earnings 10"); unread dot 6 px `accent` top-right.
- **Range chips:** segmented control with 1M 3M YTD 1Y 2Y All.

### B41. AssumptionPanel and CalcChain (T5)
- **Panel:** opens as a SideDrawer titled "Edit assumptions"; each input row: label + `i`; a slider (track `chart-track`, filled `accent`, thumb 16 px) with the numeric input at right; under the slider three markers and captions: Default (▲ `text-muted`), Historical (● `chart-benchmark`), Industry (◆ `chart-revenue`), each clickable to set the value; Decrease / Increase steppers. Live result card pinned at the top: result value (`kpi`), vs price chip, verdict.
- **CalcChain:** ordered rows (number, label, expression in `mono` `text-muted`, value right-aligned `table` 600), a 1 px rule before the final row, final row in `body-strong`; "Copy as text".

### B42. EntityName, avatar, CompactRow
- **EntityName:** avatar (ScoreMark 24 for companies, fund mark 24 for funds, initials circle for others) + name (`label`, truncate) + symbol chip (`caption`, `surface-muted`, uppercase); hover shows the copyable raw id; click opens the drawer.
- **CompactRow (< md):** 64-72 px: line 1 avatar + name + primary value right; line 2 symbol chip + 2 secondary values (e.g. "▲ 1.2% · 63% above FV") right; chevron.

### B43. Settings editor (T6)
- Left grouped menu 240 px (groups with `h3`), master list 360 px (search, filter chips, rows with status dot + name + one-line summary e.g. "Every day at 22:30 · last OK"), detail card (form). Each field: label, help text (`caption`), control, and on the right "Default: X · Current: Y" in `caption`; changed fields marked with a 3 px `accent` left bar. Summary visual at the top of the master list (B33/B34/B21…). Sticky save bar at the bottom of the viewport: "2 changes · Validate · Save with note"; only appears after a change.

### B44. PromptCard
- Inline card with a 3 px `accent` left edge, `h3` question ("30 holdings have no thesis"), one `body` sentence of value ("A thesis lets the app score each holding against your reasons"), primary action, "Not now". Max one per page, dismissible for 30 days.

---

## C. Page specs

How each entry reads: **Q** the page's one question · **Strip** answer-strip tiles with `source field` (endpoint paths from `lib/api/schema.d.ts`; `/x` = GET unless marked POST) · **Hero** the one main visual · **Panels** in order (L = left column, R = right, F = full width) · **Table** columns and presets · **States** limited / empty / error. Layouts follow the page-type wireframes in C.0; a page only gets its own wireframe when it departs from its type. Instrument names always come from the row's `name` field via `EntityName` (`name ?? humanise(ticker)`, symbol from `shortTicker()`); a rendered `_EQ` string fails the route-walk test.

### C.0 Shell and page-type wireframes

**Shell (≥ lg).** Top bar 56 px on `page`: logo mark 24 · destinations as text links (`label`, active `text` + 2 px accent underline) · right: search field "Search or jump to… ⌘K" 280 px · Inbox bell with count badge · ⚙ · avatar. No left app sidebar (the only sidebar is the entity sidebar on T2). Page header below: eyebrow (area) · h1 · one-line purpose (`body`, `text-muted`) · right-aligned primary action; then TabBar. **Mobile:** top bar 52 px with logo, search icon, Inbox bell; bottom bar 64 px (Portfolio · Research · Markets · Inbox · More), icons 24 + `caption` label, active `accent`.

```
T1 Dashboard — desktop 1440                          T1 — mobile 390
┌──────────────────────────────────────────────┐     ┌──────────────────────┐
│ ◆ Portfolio Companies Research Markets …  ⌘K 🔔⚙│     │ ◆            🔍  🔔3  │
├──────────────────────────────────────────────┤     ├──────────────────────┤
│ Portfolio                                     │     │ Portfolio            │
│ Overview                        [Primary act] │     │ Overview             │
│ Long-term growth, 30 holdings · Edit          │     │ ‹Overview Holdings ›│ chips
│ Overview  Holdings  Performance  Income  … ▾  │     │ ┌────────┬─────────┐ │
│ ┌────────┬────────┬────────┬────────┐         │     │ │ tile   │ tile    │ │
│ │ tile   │ tile   │ tile   │ tile   │ strip   │     │ ├────────┼─────────┤ │
│ └────────┴────────┴────────┴────────┘         │     │ │ tile   │ tile    │ │
│ ┌──────────────────────────────┐┌──────────┐  │     │ └────────┴─────────┘ │
│ │ Hero chart (8 col)  1M 3M 1Y ││ side (4) │  │     │ ┌──────────────────┐ │
│ │                              ││          │  │     │ │ Hero 220 px      │ │
│ │ Data · Learn · Updated …     ││          │  │     │ └──────────────────┘ │
│ └──────────────────────────────┘└──────────┘  │     │ card                 │
│ ┌─────────────────┐┌─────────────────┐ 6 + 6  │     │ card                 │
│ └─────────────────┘└─────────────────┘        │     │ CompactRow list      │
│ Grouped table  [Preset A|B|C|Custom]  🔍 ⚙    │     ├──────────────────────┤
└──────────────────────────────────────────────┘     │ ▣  ▣  ▣  ▣  ⋯  bar  │
                                                      └──────────────────────┘
T2 Entity report — desktop                           T2 — mobile
┌────────────┬─────────────────────────────────┐     ┌──────────────────────┐
│ [search ▾] │ Section 2                        │     │ ◉64 Apple  AAPL      │
│   ◉ 120    │ Valuation        3 of 6 checks ▮▮│     │ USD 342.00 ▲ 0.8%    │
│ Apple      │ ┌──────┬──────┬──────┐           │     │ [FV USD 243 · 41% ↑] │
│ AAPL NASDAQ│ │ KPI  │ KPI  │ KPI  │           │     │ [Ask] [Research]     │
│ USD 342.00 │ └──────┴──────┴──────┘           │     ├──────────────────────┤
│ ▲ +0.84%   │ ✕ Above fair value — …           │     │‹1 Overview 2 Valuat›│ sticky
│ [FV chip]  │ ✓ P/E below peers — …            │     │ section content,     │
│ [Ask][Res] │ ┌──────────────────────────────┐ │     │ one column           │
│ ── held ── │ │ chart                        │ │     │                      │
│ CZK 4,210  │ └──────────────────────────────┘ │     │                      │
│ 1 Overview │ … next section                   │     │                      │
│▌2 Valuation│ Data status: dataset · updated … │     │                      │
│ 3 Future   │                                  │     │                      │
└────────────┴─────────────────────────────────┘     └──────────────────────┘
T3 Feed                      T4 Screener                  T5 Workbench
┌───────────────────────┐   ┌───────────────────────┐   ┌───────────────────────┐
│[All 84][Earnings 10•] │   │[Idea][Idea][Idea]     │   │ Result card: kpi +    │
│ Today                 │   │ ┌Describe a screen…┐  │   │ verdict + [Edit ⟶]    │
│ ◉ Apple · Earnings 2h │   │ [chip][chip][+Filter] │   │ ┌chart / dumbbell───┐ │
│ Headline serif 17     │   │ 23 matches · 3 new    │   │ └───────────────────┘ │
│ two-line summary…     │   │ ◉ Name  … grid ⇄ list │   │ CalcChain             │
│ ◉ 12 structure updates│   │ ◉ Name                │   │ Saved versions        │
└───────────────────────┘   └───────────────────────┘   └───────────────────────┘
T6 Settings — desktop: [group menu 240][master list 360 + summary visual][detail form card] + sticky save bar
T6 — mobile: group menu → list screen → detail screen (each a full screen with back arrow); save bar fixed above the bottom bar.
```

### C.1 Portfolio

**Overview `/`** (T1, ≤ 2.7k px) — wireframe below.
- Q: How is my portfolio doing, and why?
- Under h1: strategy one-liner from `/config/strategy` (`horizon_years`, positions count) + "Edit" → Settings › Strategy.
- Strip: Value `PortfolioOverview.base.total` (display-hero, change from last two `history.points`) · Return vs benchmark `/portfolio/performance windows[label=1Y].twr.value` vs `.benchmark` (VerdictLine `returnVsBench`) · Unrealised P/L `account.unrealized_ppl` (left rule gain/loss, % of `invested_cost`) · Income next 12 m `/portfolio/dividends summary.next_12m` (caption "est." share from `next_12m_estimated`).
- Hero (8 col): B8 portfolio vs benchmark, indexed, `history.points` + `benchmark`, lead/lag zones on, range 1Y. Side (4 col): B20 risk score `/portfolio/risk/score` + 3 mini tiles from `RiskReport.tiles[]` (volatility, max drawdown, beta) each with `verdict.phrase`.
- Panels: L Scorecard — B4 240 aggregate from `/portfolio/scorecard` with selected axis, "Lifting" (gain bars) / "Holding back" (loss bars) top 3 each (B14), `AxisAggregate.explanation` as VerdictLine. R Why it moved — B15 contribution `/portfolio/contribution holdings[].contribution_base`, top 8 + others, VerdictLine `contribution`. L Allocation — B17 treemap sector → holding coloured by day move (`/portfolio/holdings/prices day_change_pct`), toggle P/L. R What happened — B36 × 8 from updates feed, Structure hidden, "All updates →" Markets › News?holdings=1.
- States: no history → hero LimitedState "Needs 5 data points…"; no benchmark → single series, no zones, chip "No benchmark set".
```
┌ Value CZK 40,442 ──┬ 1Y +30.0% ───┬ Unrealised ──┬ Income 12m ─┐
│ ▲ +0.84% today     │ vs S&P 17.3% │ +CZK 6,120   │ CZK 1,180   │
├────────────────────┴──────────────┴──────────────┴─────────────┤
│ Portfolio vs S&P 500  [Indexed|Value]  1M 3M YTD 1Y 2Y All     │ Risk score
│  ╱‾‾╲__╱‾‾‾‾‾ Portfolio 163.2                                  │ ▓▓▓▓▓░ 78/100
│ ╱ - - - - - - S&P 111.2                                        │ vol · mdd · β
├──────────────────────────────┬─────────────────────────────────┤
│ Scorecard ◉240  Lifting/Hold │ Why it moved ◀━━ ┃ ━━▶          │
├──────────────────────────────┼─────────────────────────────────┤
│ Allocation treemap           │ What happened (8 cards)         │
└──────────────────────────────┴─────────────────────────────────┘
```

**Holdings › Positions `/holdings`** (T1)
- Q: What do I hold and what needs a look?
- Strip (5): Value `account.total_value` · Invested `account.invested_cost` · Unrealised `account.unrealized_ppl` (+%) · Today Σ value × `day_change_pct` · Cash `account.cash` (weight caption).
- Hero: none by default (the table is the page); "Map" toggle swaps in B17.
- Banner (only when every `target_weight` is null): B44 "No target weights set · Set targets".
- Table (B35, 25/page, group by none | sector | verdict): presets **Check-in** (default: EntityName + B4 24 avatar, last event chip D.1, B9 sparkline, price vs fair chip `insight.fair_upside`, thesis action) · **Returns** (value, weight, P/L, P/L %, today) · **Income** (`yield_on_price`, `yield_on_cost`, `income_12m`, `next_payment`) · **Fundamentals** (P/E, growth, ROE, D/E) · **Targets** (weight, `target_weight`, `drift`, `drift_status` chip; hidden until a target exists) · Custom.
- Row click → B38 drawer (Overview · Position · Updates · Notes · Full page).
- Mobile: B42 CompactRow: name + value / P/L % + vs-fair chip.

**Holdings › Watchlist `/watchlist`** — Strip: items · nearest to buy target · above sell target. Table: EntityName, price, 7D, 3M, B9 sparkline with fair value `/watchlist/fair-value`, `buy_target` / `sell_target` as a range bar (price marker inside low…high), `reason`. Empty: B44 "Add companies from Discover" + one worked example row in `surface-muted`.

**Holdings › Transactions `/transactions`** — Strip: deposits, withdrawals, dividends, fees (from `totals[]`). Hero: B13 net flow by month (signed gain/loss). Panel F: calendar heat (52 weeks × 7, B23 cell style, value = net flow). Table: date, type chip (`type`/`subtype` via LabelMap), EntityName, quantity, price, amount, fees; filters by type; 25/page; CSV.

**Holdings › Funds `/funds`** — table of held funds: fund mark 24, name, weight, OCF, look-through status chip, lag days (from `/portfolio/xray funds[]`); row → `/funds/[isin]`.

**Performance › Returns `/performance`** (T1)
- Strip: TWR 1Y `windows[].twr.value` (+ annualised) · Money-weighted `xirr.value` · Gain `gain` · Costs `costs.total` (caption "x% of average value").
- Hero: B8 value with cost-basis StepLine (`values[]`; toggle Indexed vs benchmark `index[]`/`benchmark_index[]`), range chips map to `windows[].label`.
- Panels: F B16 returns waterfall (unrealised, realised, dividends, currency, fees → total; from `/portfolio/performance/positions` sums). L B12 underwater (from `index[]`). R monthly returns heatmap (years × months, diverging scale, moved from Overview). F rolling 90-day return B8 single series `rolling_90d`. F Positions table (open / closed segmented: invested, proceeds, dividends, fees, realised, unrealised, total, annualised).
- States: `ledger.status` ≠ complete → one chip "Ledger has breaks · Ledger tab".

**Performance › Contribution `/performance/attribution`** — Strip: start → end value, gain, active vs benchmark `benchmark.active_total`. Hero: B16 bridge (`bridge.start_value`, price realised/unrealised, flows, residual → `end_value`). Panel: B15 per holding `holdings[].total` with toggle price / fx / income / fees. Not-attributable holdings: one collapsed row "25 holdings couldn't be attributed".

**Performance › Core vs stocks `/performance/core-vs-stocks`** — Strip: core return, stocks return, annual gap `annual_gap_points` vs `gap_limit_points` (B21). Hero: B8 two series (core `chart-benchmark` dashed, stocks `accent`). `too_early` → LimitedState with `message`.

**Performance › Lots & tax `/performance/lots`** — Strip: realised gain, exemption headroom this year (`exemption[].headroom`, B21 vs `limit`), lots passing the time test within 3 months. Hero: lot age ladder: one row per lot, B21-style bar from `bought_on` to `passes_on` with today marker, passed lots `gain`. Table: lots; realised trades below.

**Performance › Ledger `/performance/ledger`** (T6-like check page) — B5 rows: position breaks, cash breaks (`cash_breaks_total`), unexplained cash, fees not captured; each with "See the figures" → table.

**Income `/portfolio/income`** (T1)
- Strip: Next 12 m `summary.next_12m` (display-hero, VerdictLine `income12` with `change_vs_trailing`) · Last 12 m `trailing_12m` · Monthly average `monthly_average` · Yield `yield_on_price` · Yield on cost `yield_on_cost`.
- Hero: B13 ForecastBars `months[]` (`received` accent, `declared` accent outline, `estimated` forecast hatch; `phase` sets the divider), Monthly / Annual (`years[]`), hover panel lists the month's payments.
- Panels: L B24 calendar `/portfolio/dividends/calendar events[]`; R Upcoming list (next 8 events: date, EntityName, kind, amount, est. chip). F Contributors: B14 `holdings[].next_12m` desc, top 10 + others (one list). L Quality tiers: B29 share bar of `tiers[]` + table (`growth_5y`, `years_without_cut`, `score`). R Forecast accuracy: B21 `accuracy.hit_rate` vs 90%.
- Banner: `coverage_sentence` as one line with expand listing names.

**Valuation `/portfolio/valuation`** (T1)
- Strip: Coverage `fair-value covered/total` (first, VerdictLine `coverage`) · Gap vs fair value `difference` (VerdictLine `portfolioFair`) · Overvalued weight `overvalued_weight` · Stale `count(holdings[].stale)`.
- Basis switcher (segmented: My scenarios · Analysts · DCF · Multiples) above the strip; the basis is named in every sentence.
- Hero: B19 price vs fair value, dots sized by `weight`, labelled; B19 below 3 placed → LimitedState. Secondary: price-vs-fair history (B8 + B9) only when `history[]` ≥ 30 days.
- Panel F: multiples vs benchmark — three small B14 bar pairs (P/E, P/B, yield: portfolio vs each `comparisons[]`).
- Table B35 grouped: Undervalued · About right · Overvalued · Stale · No fair value (collapsed, rows carry "Add a scenario"). Columns: EntityName, price, fair value, gap chip, basis, analysts `consensus.count`, updated.

**Allocation › Exposure `/xray`** — Strip: look-through coverage `classified_weight`, cash `cash_weight`, fund fee drag `fees.annual_drag`, issuers scored `scored_issuers/issuers_total`. Hero: B18 Sankey portfolio → sector → industry → holding (`/xray/diversification nodes/links`), level toggle. Panels: L geography B14 (`geography.rows`); R dimension switcher (sector, country, currency, asset class from `views[]`) as B29 / B14; `reconciles=false` → warning chip. Unknown weight always drawn as hatched "Unknown".
**Allocation › Diversification `/xray/diversification`** — Strip: top-3 share `concentration.top3_share` (`sentence`, `band`), effective n `effective_n` (B20 band D.1), largest `largest` + weight. Hero: B31 Lorenz. Panel: B14 top 10 issuers.
**Allocation › Concentration `/xray/concentration`** — level tabs from `levels[].level`; per level: B14 `top_items`, B21 HHI vs thresholds, B15 over/underweights.
**Allocation › Overlap `/xray/overlap`** — Strip: shared issuers `shared_count/issuer_count`, shared weight, redundant weight. Hero: B22 fund × fund overlap. Panel: B14 issuers by `overlap_weight` with channel chips; B28 removal impact (`effective_before` → `effective_after`).
**Allocation › Benchmark `/xray/benchmark`** — Strip: overlap with benchmark, portfolio-only weight, benchmark-only weight. Hero: B15 active weights by dimension (`dimensions[].lines`), dimension tabs. `available=false` → LimitedState with `reason`; `age_days` in footer.
**Allocation › Fundamentals `/xray/fundamentals`** — Strip: covered weight, equity weight. Hero: stack of B19 strips per metric (`metrics[]`, `thin` → limited chip), basis tabs.
**Allocation › Supply chain `/xray/supply-chain`** — Hero: B32 graph (`nodes`, `edges`); Panel: linked exposure B14 (`linked[].combined_weight`); mobile list first.
**Allocation › Your data `/xray/inputs`** — T6-style list of owner inputs with status dot and edit drawer.

**Risk › Overview `/risk`** (T1)
- Strip: Risk score `score.score` vs `benchmark_score` (B20 gradient, `band`, `tone`) · then `tiles[]` (volatility, max drawdown, Sharpe, beta, diversification): `value`, `benchmark_value` caption, `verdict.phrase`.
- Hero: B20 risk gradient bar with portfolio and benchmark markers; `sub_scores[]` as B21 rows under it.
- Panels: L B14 per-holding risk (`holdings[].volatility`, toggle max drawdown / beta) with portfolio reference line; R B12 drawdown path `drawdown_path`; F correlation: two lists `together[]`/`least_together[]` + B22 `correlation.values` (only when ≥ 60 shared days, else LimitedState with `window.observations`).
- One caveat banner per page (ReasonOnce).
**Risk › Today `/risk/ex-ante`** — Strip: forecast volatility `volatility_forecast` vs `limits.max_volatility` (B21), effective bets vs `min_effective_bets` (B21), diversification ratio. Hero: B14 `positions[].risk_share` vs weight (paired bars: weight `chart-track`, risk share `accent`), status chips. Panel: by tier share bar; model comparison (EWMA, Ledoit-Wolf, realised) as three tiles.
**Risk › Bad months `/risk/tail`** — Strip: 1-month ES 95% `es_95_month`, Sortino, Calmar. Hero: B25 monthly returns histogram with worst-5% tail shaded and VaR/ES reference lines (`lines[]`). Panels: ES contribution B14 `contributions[].es_share`; drawdown probability table (`drawdown[]` by years: p50/p95, recovery weeks) as B21 rows.
**Risk › Past episodes `/risk/replay`** — Hero: B14 `episodes[].pnl` (loss bars), `drawdown_limit` reference line, breach chip; row expand: worst 5 holdings, real vs proxied weight share bar.
**Risk › Factors `/risk/factors`** — Strip: R², alpha (t-stat caption). Hero: B15 `tilts[]` portfolio vs benchmark (accent / benchmark grey), flagged chip. T5 panel "Move the factors" → POST `/portfolio/risk/factor-shock` with result tiles.
**Risk › Stress `/risk/stress`** (T5) — scenario list left (`/portfolio/scenarios/standard`), result: B14 `lines[].pnl_pct` and total tile; custom shock opens B41.
**Risk › Themes `/risk/themes`** (T5) — theme picker chips → B14 result.
**Risk › Does it hold up `/risk/validation`** — Strip: confidence chip (ok / reduced / untested), weeks tested. B5 rows per backtest (`hits` vs `expected_hits`, p-values in "See the figures"), calibration B21 `realised_over_forecast` vs 1.0.

**Decide** (T5; one tab with a mode query param, URL fixed in R5; old `/risk/{whatif,rebalance,pretrade,deposit}` and `/xray/proposed` redirect)
- Mode segmented control at top. Each mode: inputs card (left 4 col) → result (8 col): result tiles (vol before/after, effective bets before/after, cash after) → B28 dumbbell of weights before → after → trades table (action chip Buy/Sell, EntityName, shares, value, cost) → assumptions list (`assumptions[]`).
- Empty form: pre-filled example result in `surface-muted` with "Example" chip.
- Sources: POST `/portfolio/scenarios/whatif`, `/portfolio/rebalance`, `/portfolio/pretrade`; GET `/portfolio/risk/deposit`, `/risk/over-cap`, `/risk/sizing`.

### C.2 Companies and funds

**Company `/companies/[t]`** (T2). Sidebar and header from one summary request (`/companies/{t}/overview` + `/holdings/{t}` when held). Sidebar fields: ScoreMark from `/checks axes[].score`; price `last_close` + day change; fair value chip (basis per D.1); held card `position.value_base`, `ppl`, `weight`. Section check counts from `/checks axes[].{passes,applicable}`.

1. **Overview** — Strip: Price + 1Y return, Fair value + gap, Market cap, Next event (`upcoming[0]`). Hero B8 price (line; candles toggle B11), fair value B9, events lollipops. Panels: F rewards / risks (`/checks risks[]`, `rewards[]`) as two B5 lists of max 3 + "See all checks" → dialog; L latest results note + 3 question chips (open Ask scoped); R shareholder returns 7D / 1Y vs industry / market (table with pass sentence) and price volatility vs industry/market (B14 three bars); F About (`profile[]` key/value grid 2 col); F Competitors: 4 cards (`peers_confirmed`) with B4 64, name, price, gap chip.
2. **Valuation** — header "n of 6 checks". Hero B20 valuation gauge on headline basis. Panels: F "All fair values" B19 single-company strip (DCF `valuation/defaults`, multiple path `valuation/multiple`, analyst mean `/consensus latest.point.mean`, thesis target) each labelled with basis, price line; L B14 peers (`pictures.peers.bars`, median line, growth column); R B25 industry histogram (`pictures.histogram`); L multiple history B8 (`pictures.history.points`, windows median lines); R fair multiple tile (`fair_multiple.fair_pe`, `implied_price`, `r_squared` caption); F B26 analyst targets (`/consensus history`, `prices`, `latest.agreement_text` VerdictLine, `track.hit_rate`); F Scenarios: answer card + "Edit assumptions" (B41: `base_fcf`, `growth[]`, `terminal_growth`, `discount_rate`) + CalcChain + saved versions (`valuation/saved`). Mobile: bear/base/bull stacked cards, never a wide grid.
3. **Future** — Strip: revenue growth E, EPS growth E, analysts (`/estimates points[].analyst_count`). Hero: B8 revenue and earnings history + forecast (forecast dashed, low–high band at 12%), series chips Revenue · Earnings · FCF. Panels: growth checks (B5 for axis Future), B27 earnings surprise (`surprise`) lollipops ±, estimate revisions B15.
4. **Past** — Hero B18 income statement Sankey with year strip (`/statements`). Panels: past checks B5; quarterly history table (columns = periods, rows = lines, sticky first col); segments donut ≤ 7 (`/segments breakdowns[].members`, LabelMap) + B15 change vs prior year.
5. **Health** — Panels: paired bars short-term assets vs liabilities, long-term assets vs liabilities (B14 pairs); debt-to-equity history B8; debt maturity ladder B13; capital allocation B16 per year (`/capital-allocation years[]`: FCF → dividends, buybacks, capex, net debt change) with "returned x% of FCF" VerdictLine; health checks B5.
6. **Dividend** — Strip: yield, years without cut `years_without_cut`, 5Y growth `growth_5y`, payout `payout.earnings`/`cash`. Hero B13 `annual[].per_share` (partial year hatched). Panels: yield vs market quartiles + peer median B14 (`yield_vs_peers`, VerdictLine `yieldMkt`); "Buy before" countdown tile (next ex-date from `schedule[]`); received by you table (`received[]`); dividend checks B5.
7. **Ownership & management** — Panels: management tiles (CEO, tenure, pay `compensation[].total`, ownership); CEO pay vs peers B14; board table (`roles[]`); insider trades B27 + net bar (`/disclosures/holdings/{t}/ownership`, `/insiders net[]`) with verdict; holder types B29; top holders table; related-party figures collapsed.
8. **Research & thesis** — Strip: thesis state (`BoardRow.state` chip), assessment overall (`/assessment overall` with `overall_coverage`), conviction. Panels: KPI bullets B21 (thesis `kpis[]` current vs threshold); assessment components B14 (`components[].score`, coverage chip); case items list (`/case items[]` kind chips + confidence); report conclusion (`/report conclusion`, `conflicts[]` as warning rows); research reports for this ticker (list).
9. **Network & reference** — B32 (`/network edges[]`, `evidence_tier` → solid/dashed), patents B13 (`patent_by_year`); reference: listings table, identifiers, price completeness B23 row, corporate actions table.
- Every section ends with the data-status footer (dataset · last updated · timeframe · source).
- States: company not covered → sidebar + one PromptCard "Add to coverage"; a section without data shows its header and one LimitedState, never empty panels.

**Fund `/funds/[isin]`** (T2, fund mark axes D.1). Sections: 1 Overview (`/profile fields[]` grid, `have/of` coverage chip, price chart) · 2 Holdings (`/holdings`: B17 top holdings, B29 sectors / countries / asset classes, `residual_weight` as Unknown, `reconciles` chip) · 3 Performance (`/performance`: periods table fund vs benchmark with difference sign-coloured, B8 `series[]`, tracking error tile, NAV premium tile) · 4 Style (`/style`: 3×3 size × value-growth grid as B22-style cells with weight, concentration tiles) · 5 Bond (only when `duration` present: tiles maturity, duration, credit quality; B13 `rate_buckets`; B29 `credit_spread`) · 6 Checklist (`/checklist criteria[]` as B5: good → ✓, watch → !, concern → ✕, info → i; `summary` as VerdictLine).
**Fund compare `/funds/compare`** — up to 4 funds: fund marks 120 side by side, B28 per metric, overlap B22.
**Find a fund `/funds/research`** (T4) — filters: OCF, size, region, asset class; results table with fund mark 24.

### C.3 Inbox `/inbox` (T1 + T3)
- Q: What needs my decision?
- Strip: Needs you `InboxList.unread` + Autopilot `waiting` · Oldest (age days, B37 ageing tone) · Decided this week · Handled automatically (`/autopilot/summary kinds[].applied` sum, link System › Autonomy).
- Filter chips with counts (§5.8 list); zero-count chips fold into "Nothing waiting in n queues".
- List: B37 items grouped by company then kind; batch items for repeated prompts; item → B38 drawer (escalation `body.headline`, `rationale`, `thesis_link`, sources, proposed action Confirm / Decline).
- Empty: VerdictLine `inboxEmpty` + last 5 auto-handled items in `text-muted`.
- Mobile: review mode (one item per screen, buttons visible, swipe optional).
```
┌ Needs you 23 ┬ Oldest 16 d ┬ Decided 7 ┬ Auto 41 ┐
[All 23][Proposals 2][Escalations 1][Journal 20•][Figures 0 …]
Apple ───────────────────────────────────────────
│▌◉ Escalation · 3 h   Price fell below thesis floor
│  Why: price USD 180 < floor USD 190 · See figures
│  [Review]  [Snooze ▾]  [Dismiss]
◉◉◉◉◉ +15  Write down why you bought 20 holdings   [Start] [Dismiss all]
```

### C.4 Research
- **Theses `/theses`** — Strip: portfolio score `portfolio_score`, intact / watch / at risk counts (`rows[].state`), reviews overdue. Hero: board grouped by state (cards: EntityName, B4 24, `score`, KPI bullets mini, `open_review_items` chip). Panel: expected returns ranking B14 (`/theses/expected-returns`: return vs `return_hurdle` reference), switch ideas list (`/theses/switches`). Empty (no theses): guided start — pick holdings (checkbox list) → "Start workup".
- **Thesis workup `/thesis`** and memo / review pages (T2-lite, no sidebar mark): TOC rail left, memo body 72 ch, KPI table, claim ledger collapsed.
- **Reports `/research`** — list (T3): title, EntityName, headline, status chip, date; detail `/research/[id]` T2 with TOC rail, sources rail grouped by domain, names not ids.
- **Discover `/discover`, `/screens`** (T4) — idea cards row (aggregate B4 64, title, avatar stack of matches, "n new in 7 days" from `screens[].new_last_7d`); "Describe a screen" box → chips; filters from `/discover/catalogue metrics[]` (disabled when `known/total` < 50% with reason); results table: B4 24, EntityName, chosen column set (`/discover/column-sets`), Watch star; grid view cards with B4 120. Footer: "29 instruments covered — your holdings". Backtest `/discover/backtest` (T5): B8 screen vs benchmark.
- **Journal `/journal`** — Strip: entries, reviews due, median outcome. Table: date, EntityName, decision chip, conviction (5-dot), price at decision, change since, review date. **Scorecard `/journal/scorecard`**: `sections[]` as cards with B14 `rows[].median_change` and n chip; calibration dots (conviction vs outcome, B19 style).
- **What changed `/insights`** (T3) — area chips (`areas[]`), items: subject EntityName, what, before → after (B28 inline mini), relevance sort. `/insights/sectors`: B15 `revenue_growth` by sector, table with held weight.

### C.5 Markets
- **News `/news`** (T3) — chips by `event_type` with counts (`/news/stats by_event_type`), "Holdings only" toggle; day headers; B36 cards (`headline`, `outlet` + tier dot, `tickers` as EntityName, `sentiment_score` not shown as a number, a ▲▼ only when the story moved price); Structure grouped. Header strip: items today, unclassified, disputed (→ Inbox). Tabs: Feed · Stories (`/threads`: story cards with item count and state) · Wire (`relevance` sort, `language` chip, translated `gloss`) · Brief (`/news/briefs` + themes B14 `attention`) · Ask · Sources (`/news/sources/health`: B23 per source by day + `consecutive_failures` chip).
- **Macro `/macro`** — Strip: portfolio mapped weight `/macro/coverage/portfolio mapped_weight`, series updated this week. Hero: B30 small multiples grouped (Rates · Inflation · Growth · Commodities · FX) with LabelMap titles and units; `revised` → hatched band; "No change computed" rows → one line.
- **Filings `/disclosures`** — landing: Strip: alerts this week (by company), coverage share, issuers. Panels: alerts by company B14; coverage B23 (`/disclosures/data/coverage cells[].covered_share`). Issuer picker → issuer page T2 (timeline B27, snapshot, statements). Tabs: Ownership (B29 lanes `percent_of_denominator`) · Insiders (B15 `net[].net_value`, clusters list) · Customers & suppliers (B18 `flows[]`) · Deals (B27 timeline + table) · Estimates (B27 `surprise_pct`) · Evidence screen (T4, `matched_weight` tile) · Look-through (B15 direct vs fund weight stacked) · Replay (T5). Decimal strings parsed once in the adapter.
- **13F `/investors`** (T4 list) — rows: manager mark 24 (concentration), name, `reported_value_usd`, positions, change vs `previous_value_usd` (signed). Manager page T2: B17 positions treemap, B15 position changes, overlap with you.

### C.6 Briefings
- **Daily & weekly `/reports`** — timeline list: date column, type chip, `headline`, `story_count` and `citation_count` chips, "Nothing material" rows collapsed into one line per week. Detail `/reports/[id]` T2: TOC rail by company (h2 = company name), sources rail grouped by domain with counts, max height 480 with "Show all".
- **Monthly reviews `/reviews`** — same list; detail T2 with sections (performance, changes, theses, next steps).

### C.7 System
- **Autonomy** (Engine ledger + Autopilot views; URL fixed in R7) — Strip: items per week vs target (`scorecard.items_per_week(_target)`, B21), automatic share, oldest open days, late jobs. Hero: B21 rows for every scorecard target with ✓/✕. Panels: per-kind table (`kinds[]`: mode chip, decided, applied, escalated, reverted, precision B21 mini); calibration (`calibration[].bins` reliability plot: expected vs observed dots on a diagonal, `ece` tile, verdict); decisions feed T3 (`/engine/ledger`).
- **Activity `/activity`** — Strip: AI spend `costs.total_usd` of `cap_usd` (B20 budget, VerdictLine `budget`), jobs success (VerdictLine `jobs`), gaps. Hero: B23 job runs × last 21 days grouped by job group. Panels: B33 week timeline; cost by job B14 (`by_job_type`) and by model share bar; job log T3 paginated (`status` chip, EntityName, `failed_step` in Details). Job run `/jobs/[id]` T2-lite: steps as B5 rows, cost, attempts.
- **Data sources `/data-sources`** — B5 rows per source (`state`, quota B21, `hard_blocked` → ✕ with plain sentence instead of HTTP code); usage B13 credits per day; coverage share bars per group (`coverage_ratio`).
- **Data quality `/quality`** — Strip: affected weight `affected_weight`, open issues, snoozed. Hero: issues as B5 rows grouped by `kind` (sentence + `fix` action), ordered by `weight`. Panels: insight coverage B23; not covered list; Filings coverage; Overview diagnostics (return-data completeness).
- **Verification `/verification`** — B5 rows: zero-cost gate (`passed`, `violations`), domain gates (`allowed`, `samples`, `failures`), signal gates (`publishable`, `precision` B21 vs target, `sampleSize`); B22 rule × source.
- **Release `/release`** — Strip: ready (✓/✕), activatable count, facts matching `facts_matching/facts_checked`. Hero: feature table as B5 rows (`features[].status`, `can_activate` → Activate button); release checks per week B23.
- **AI decisions `/ai-decisions`** — T3 feed of decisions with veto action (vetoes surface in Inbox).
- **API status `/status`** — single card: status dot + `version` + uptime sentence.

### C.8 Settings (T6) — summary visual at the top of each master list
| Editor | Master rows (one-line status) | Summary visual | Detail highlights |
|---|---|---|---|
| Strategy | sections: Targets, Rules, Risk, Pre-trade | B29 donut of `target_positions` + `cash_target`, live 100% check | rule rows with "n holdings breach" |
| Theses | holding + state chip | state share bar | KPI rows with threshold sliders (B41 style) |
| Holding profiles | EntityName + valuation source | coverage share bar | form grouped Identity · Pricing · Valuation (1,273 inputs → one profile at a time) |
| Event rules | event type + level | B34 severity matrix | per-holding overrides list |
| Checks | check + threshold | "n holdings would change state" live tile | threshold slider with default / current |
| News sources | source + tier + health dot | trust bars B14 | tier, language, fetch cadence |
| Calendar | exchange + next session | B33-style week of sessions | holidays table |
| Research profiles | profile + one-line summary | — | accordions per section |
| Verification | gate + target | B21 rows | samples, thresholds |
| Jobs | job name (not key) + "Every day at 22:30 · last OK" | B33 week timeline | cron in `mono` + human sentence, cap |
| Budget | cap, warn, per-job cap | B20 burn band | price table |
| Prompts | prompt name + model + updated | — | read view; code view on expand only |
| Notifications | channel + rules count | week heat of expected alerts (B23) | quiet hours |
| Autonomy thresholds | kind + mode | B21 precision vs threshold | (moved from Engine ledger) |
| Starter screens | screen + matches | — | (moved from Discover) |
| History & export | domain + last change | B27 changes timeline | diff view (`/config/{domain}/diff`), export |
- All editors load clean (save bar hidden until a field changes); validation errors as B5 ✕ rows above the save bar.

---

## D. Formulas, thresholds and sentence templates

### D.1 Bands and formulas
| Item | Formula / band | Used in |
|---|---|---|
| ScoreMark tone | mean of known axis scores (0-6): < 2.5 weak, 2.5-4.0 mixed, > 4.0 strong | B4 everywhere |
| Axis shown as unknown | > 50% of the axis's applicable checks unknown | B4 |
| Price vs fair value | `(price − fair) / fair`; ≤ −20% undervalued, −20…+20% about right, ≥ +20% overvalued | B19, B20, Valuation chips |
| Headline fair value basis | user's saved scenario → analyst consensus mean if ≥ 3 analysts → default DCF; always named | company header, Valuation |
| Stale fair value | inputs older than the Checks setting (default 180 days) | Valuation groups |
| Volatility verdict | portfolio vol ÷ benchmark vol: < 0.8 calmer, 0.8-1.2 similar, > 1.2 more volatile | Risk, Overview |
| Max drawdown verdict | portfolio MDD ÷ benchmark MDD over the same window: < 0.8 shallower, 0.8-1.2 similar, > 1.2 deeper | Risk |
| Sharpe verdict | < 0 negative, 0-0.5 weak, 0.5-1.0 fair, > 1.0 good (1y window, CZEONIA rf) | Risk, Overview |
| Beta verdict | < 0.8 defensive, 0.8-1.2 moves with the market, > 1.2 amplifies the market | Risk, company |
| Top-3 concentration | top-3 issuer share: < 25% well spread, 25-40% concentrated, > 40% highly concentrated | Allocation, Risk |
| Effective holdings | `1 / Σ wᵢ²` over look-through issuers; band: < 10 concentrated, 10-25 moderate, > 25 diversified | Allocation › Concentration |
| Risk score | per insight plan I12 (published method); bands 0-25 low, 26-50 moderate, 51-75 high, 76-100 very high | B20 |
| Drift | `weight − target` in pp; inside band = ≤ tolerance; outside = verdict "x pp over/under" | Holdings, Strategy |
| Budget projection | `spend_to_date / elapsed_days × days_in_month`; on track ≤ 100% cap, tight 100-110%, over > 110% | System, Settings › Budget |
| Income change | next 12 m vs last 12 m received, % | Income hero |
| Yield vs market | company yield vs market bottom 25% / top 25% quartiles of covered universe | Company › Dividend |
| Check-in "last event" | most recent item in 7 days with severity ≥ 2, or a story where the holding is the primary entity with relevance ≥ 0.6, excluding kind Structure; else "No notable event in 7 days" | Holdings Check-in |
| Feed kind "Structure" | relationship kinds subsidiary, parent, ultimate_parent, identifier, listing | B36 grouping |
| Inbox ageing | ≥ 7 days warning edge, ≥ 14 days loss edge | B37 |
| Fund mark axes (0-6 each) | **Cost**: OCF ≤ 0.10% 6, ≤ 0.20% 5, ≤ 0.35% 4, ≤ 0.50% 3, ≤ 0.75% 2, ≤ 1.00% 1, else 0. **Diversification**: effective issuers ≥ 200 6, ≥ 100 5, ≥ 50 4, ≥ 25 3, ≥ 10 2, ≥ 5 1, else 0. **Tracking**: abs 1y tracking difference ≤ 0.10 pp 6, ≤ 0.25 5, ≤ 0.50 4, ≤ 1.00 3, ≤ 2.00 2, ≤ 3.00 1, else 0 (n/a for active funds without a benchmark). **Size**: AUM ≥ EUR 5bn 6, ≥ 1bn 5, ≥ 500m 4, ≥ 100m 3, ≥ 50m 2, ≥ 10m 1, else 0. **Data quality**: share of fund weight with known look-through ≥ 99% 6, ≥ 95% 5, ≥ 90% 4, ≥ 75% 3, ≥ 50% 2, > 0% 1, else 0 | Fund mark (B4) |

### D.2 Minimum data per visual
| Visual | Minimum | LimitedState sentence |
|---|---|---|
| Time-series line | 5 points in range | "Needs 5 data points in this range, has {n}." |
| Fair-value history | 30 stored days | "Fair values are recorded daily since {date}; the chart appears after 30 days." |
| Drawdown | 20 observations | |
| Volatility | 20 daily returns; VaR/ES 250; beta and correlation 60 shared days | "Needs {min} shared trading days, has {n}." |
| Correlation matrix | 3×3 and ≥ 50% of pairs with 60 shared days | |
| Peer bars | 3 peers (hide below 2) | "Needs 3 comparable peers, has {n}." |
| Industry histogram | 20 companies | |
| DotStrip | 3 placed holdings | "Only {n} holdings have this figure." |
| Sankey | 3 links over ≥ 2 levels | falls back to ranked bars |
| Treemap | 3 items | falls back to ranked bars |
| Column/ForecastBars | 3 non-zero periods | |
| Waterfall | 2 non-zero components | |
| FanBand | 3 analysts and 4 snapshots | falls back to a range bar |
| HeatMatrix (overlap) | 2 funds with holdings | |
| BandGauge | a marker value | |
| Histogram (returns) | 20 periods | |

### D.3 Sentence templates (frontend, `lib/verdicts/*`, each with a unit test)
Figures are formatted per A.6. `{basis}` is always named. Tone in brackets.

| Id | Template |
|---|---|
| `vol` | "Swings {vol} a year, {calmer\|about as much as\|more than} {benchmark} ({bvol})." [good\|neutral\|bad] |
| `mdd` | "Worst fall {mdd} from a peak, {shallower than\|similar to\|deeper than} {benchmark} ({bmdd})." |
| `sharpe` | "{weak\|fair\|good} return for the risk taken (Sharpe {s})." |
| `beta` | "{defensive: moves less than\|moves with\|amplifies} the market (beta {b})." |
| `top3` | "Your three largest issuers are {share} of the portfolio — {well spread\|concentrated\|highly concentrated}." |
| `effN` | "Behaves like {n} equal holdings." |
| `vsFair` | "Trading {pct} {below\|above} its fair value on {basis}." [good\|bad] |
| `portfolioFair` | "{covered} of {total} holdings have a fair value on {basis}; together they trade {pct} {below\|above} it." |
| `income12` | "Expected over the next 12 months: {amount}, {pct} {more\|less} than the last 12." |
| `yieldMkt` | "Yield {y} is {in the bottom quarter\|in the middle\|in the top quarter} of covered dividend payers ({p25}–{p75})." |
| `drift` | "{name} is {pp} {over\|under} its target ({target}); band ±{tol}." |
| `noTarget` | "{n} holdings have no target weight. Set targets to see drift." |
| `contribution` | "{top} added the most ({amt}); {bottom} cost the most ({amt2})." |
| `returnVsBench` | "{period}: {ret} against {benchmark} {bret}, {ahead\|behind} by {gap} points." |
| `budget` | "{spent} of {cap} spent; on pace for {proj} by month end ({on track\|tight\|over})." |
| `jobs` | "{pct} of runs succeeded first time in the last {days} days; {failed} failed." |
| `coverage` | "{have} of {need} holdings have {dataset}; {missing} are missing because {reason}." |
| `checksHeader` | "{pass} of {applicable} applicable checks pass{; {unknown} couldn't run}." |
| `strongestWeakest` | "Strongest on {axis1} ({s1}/6); weakest on {axis2} ({s2}/6)." |
| `inboxEmpty` | "Nothing waits for you. {auto} items were handled automatically this week." |
| `limited` | "Needs {min} {unit}, has {have}." |

Backend sentences (checks, risk score, valuation verdicts) are rendered as returned; the frontend never rewrites them.
