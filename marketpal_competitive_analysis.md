# MarketPal.io — Feature Audit & Competitive Analysis for StewardAI

Explored 2026-09-29 by logging into the user's own MarketPal.io account. MarketPal is a Czech-market fintech app (co-branded "MarketPal x Chytrá Opice") combining a stock research terminal, superinvestor/13F tracker, and watchlist tool.

## Sections found

- **Dashboard** — feed driven by your Portfolio: recent earnings (EPS/Revenue surprise %, mark-as-read), notable insider trades, new 13F filings.
- **Portfolio** (`/portfolio`) — just a list of tickers you own (price, 1D change, remove). No shares, cost basis, or P&L. Explicitly labeled unfinished ("CSV import is on the way"); only powers the dashboard feed today.
- **Watchlist** (`/watchlist`) — much richer: editable Buy target / Sell target / Reason / Note columns, valuation & quality metrics (P/E, PEG, Fwd PEG, P/S, P/B, P/FCF, margins, ROE, ROIC), column picker, multiple named/saved lists.
- **Stock detail** (`/stocks/TICKER`) — profile header, price chart (1M–All), persistent "TTM snapshot" sidebar (valuation/margins/returns) visible across all tabs. Tabs: Financials (income/balance/cash flow/key metrics/margins/segmentation, annual/quarterly, chart/table toggle), Analysts, Ownership (insider trading + investors, with Form 3/4/5 and transaction-type filters), News (aggregated articles + press releases from many sources).
- **Investors** (`/investors`) — superinvestor/13F tracker (Dataroma-style): scatter plot of ~30 famous investors' outperformance vs S&P 500 vs track record, searchable table with AUM, top holdings, QoQ/1Y performance, individual investor detail pages.
- **Compare** (`/compare`) — multi-ticker fundamentals comparison with metric picker, Absolute vs YoY Relative, FY vs Quarterly, overlaid dual-axis charts.
- **Calendar** (`/calendar`) — earnings calendar, month/list view, filterable to your watchlist.
- **Alerts** (`/alerts`) — metric/event alerts, but activation is paywalled ("Subscribe to activate alerts").
- **Glossary** (`/glossary`) — ~50-term financial glossary with formulas, examples, and cross-links. Strong educational layer.
- **Pricing** (`/pricing`) — token-based subscription (3 tiers, CZK) where tokens fuel "AI reports, analyses, and other advanced features." No AI feature was found directly in nav — likely embedded in a "Report" tab or per-stock report generation not fully explored.

## Notable UX patterns

- Global Ctrl+K command palette for stock search on every page.
- Persistent valuation/margins/returns sidebar on stock pages regardless of active tab.
- Consistent chart/table toggle wherever data is charted.
- Reusable filter-chip pattern (transaction types, form types, time ranges) across Ownership/Insider views.
- Dark mode toggle; localized (Czech) descriptions with English tickers/jargon.

## Known bugs/gaps observed

- Financials charts (Revenue/Gross Profit/Operating Income/Net Income) stuck on "loading" indefinitely on at least one ticker (NVDA) — likely a slow/broken data endpoint.
- Insider Trading chart similarly stuck loading — same apparent bug pattern.
- Portfolio has no real position tracking (no shares/cost basis/P&L) — it's a tag list, not a portfolio.
- Alerts can be configured for free but require a paid plan to actually fire.

## What StewardAI could borrow

1. **Superinvestor/13F tracker** — this is MarketPal's most differentiated feature and likely absent from StewardAI. A scatter-plot "who's beating the S&P" view plus per-investor top-holdings pages is a strong engagement/discovery hook with no real-time data cost (13F filings are free/quarterly).
2. **Persistent valuation snapshot sidebar** on stock detail pages — cheap to build, high perceived value; keeps key ratios visible no matter which tab you're on.
3. **Financial glossary with cross-links** — low effort, strong onboarding/retention/SEO value; almost no apps in this space bother.
4. **Watchlist as a research tool, not just a list** — Buy/Sell target + Reason/Note fields turn a watchlist into a working thesis tracker; worth more than MarketPal's own thin Portfolio feature.
5. **Insider trading + institutional ownership tab** with Form 3/4/5 filters and top-insider leaderboard — good differentiator if StewardAI has SEC filing data access.
6. **Multi-ticker fundamentals Compare tool** with YoY-relative toggle and estimates overlay — useful for relative-valuation workflows StewardAI may not currently support.
7. **Freemium alert paywall pattern** (configure free, pay to activate) as a monetization idea, if StewardAI wants a alerting tier.

## What to avoid / do better

- Don't ship a "Portfolio" that's really just a watchlist — real position tracking (shares, cost basis, realized/unrealized P&L) is table stakes MarketPal itself hasn't delivered; doing so would be a real edge.
- Avoid the loading-spinner-forever failure mode on chart data — ensure timeouts/error states.
- If building AI-driven analysis, make the AI feature visible and discoverable in the nav rather than buried behind a token economy with no clear entry point (MarketPal's own weak spot).

## Implementation plan (against the current StewardAI codebase)

Stack: Python/FastAPI backend (`apps/backend/src/steward`, `src/api`, `worker/`) + Next.js/React/TypeScript frontend (`apps/web`) via a generated typed client. Existing integrations: SEC/EDGAR-based fundamentals (`steward/fundamentals/`), earnings (`steward/earnings/`), Yahoo-based prices (`market_routes.py`, `apps/web/src/lib/prices/intraday.ts`), an ownership reference module (`apps/web/src/lib/reference/ownership.ts`), and RSS news aggregation (`steward/config/news_sources.py`).

**1. Glossary — free, no data dependency, do first**
Static content module in `apps/web` (e.g. `src/content/glossary/`), rendered as its own route. No backend work. Good quick win / warm-up task.

**2. Watchlist with buy/sell targets & notes — free (needs only price quotes you already have)**
Extend `apps/web/src/lib/portfolio/` + `components/portfolio/` with a real Watchlist entity (separate from Portfolio): new Supabase table (`watchlist_items`: ticker, buy_target, sell_target, reason, note, list_name), a backend CRUD route beside `market_routes.py`, and reuse the existing price feed for live quotes.

**3. Real Portfolio (shares/cost-basis/P&L) — free, and your biggest edge over MarketPal**
Add `portfolio_positions` table (ticker, shares, cost_basis, date acquired), compute realized/unrealized P&L using existing price data. This is pure engineering, no new data source.

**4. Persistent valuation sidebar on stock detail pages — free-ish**
Compute P/E, PEG, P/S, P/B, margins, ROE, ROIC from data already flowing through `steward/fundamentals/` + current price. Mostly a ratio-calculation + UI task, not a new data source, for US-listed tickers already covered by your SEC/ESEF pipeline. Non-US tickers will need a fallback or explicit "unavailable" state.

**5. Insider trading (Form 3/4/5) — free, new ingestion job**
New backend job under `worker/` pulling Form 3/4/5 filings from SEC EDGAR full-text search/structured filings API (no key required), feeding into `apps/web/src/lib/reference/ownership.ts`. US-listed only.

**6. Superinvestor/13F tracker — free but the most engineering-heavy**
New module `steward/ownership/13f.py` (or extend `ownership.ts`): ingest quarterly 13F-HR filings from SEC EDGAR for a curated manager list, diff holdings quarter-over-quarter, and reconstruct historical returns from disclosed positions to compute the "outperformance vs S&P 500" metric. Budget this as multi-week, not a quick add — the data is free but the return-reconstruction logic is real work. Do this last, after the simpler wins are shipped.

**Sequencing:** Glossary → Watchlist → real Portfolio → valuation sidebar → insider trading → 13F tracker. Each step is shippable independently and the first four require no new external data source beyond what SEC/EDGAR and your existing Yahoo price feed already provide.
