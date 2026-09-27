# Ledger II — from confidence chips to an epistemics UI

**Status:** Research, proposal, and plan only — no implementation yet.
**Builds on:** `Ledger_Design_System_Redesign_Proposal.md` (Phases 6–10, implemented). This is
not a new design system — it keeps every token, component, and principle Ledger already
has, and asks a narrower question: *given what Ledger already does well, what does the next
wave of fintech, AI-native, and research-terminal products suggest StewardAI is still
leaving on the table?*

---

## Table of contents

1. [Where Ledger stands today](#1-where-ledger-stands-today)
2. [Research](#2-research)
3. [Design principles, extended](#3-design-principles-extended)
4. [Proposals](#4-proposals)
5. [Explicitly rejected](#5-explicitly-rejected)
6. [Roadmap](#6-roadmap)
7. [Success criteria](#7-success-criteria)
8. [Sources](#8-sources)

---

## 1. Where Ledger stands today

Direct read of the repo, not assumption. Phases 6–10 of the first proposal are implemented
and merged (`apps/web`, branch `claude/intelligent-hypatia-ewx4qg`):

- **Tokens & type** (Phase 6): the full Ledger palette is live in `tokens.css` — Verdigris
  accent, the vermillion/teal gain-loss pair, four dedicated confidence hues, an 8-slot
  categorical set, a diverging drift pair. IBM Plex Sans/Serif/Mono self-hosted via
  `@fontsource`.
- **Portfolio visualization** (Phase 7): a real `GET /portfolio/history` endpoint now backs
  a portfolio-vs-benchmark performance chart on Overview, **flow-adjusted** so a deposit or
  withdrawal never reads as a fake gain or loss (`steward/portfolio/history.py`). Allocation
  is a genuine single-row stacked bar (`AllocationStackedBar`) above the existing bar-list.
  Drift from target is a standalone diverging bar chart (`DriftChart`), not just an inline
  indicator. Stat tiles carry real Tremor Raw `SparkAreaChart` sparklines
  (`components/tremor/spark-chart.tsx`), fed by the same flow-adjusted series.
- **Confidence & evidence** (Phase 8): `ConfidenceBadge` renders each of Verified /
  Corroborated / Unconfirmed / Disputed in its own validated hue (not a shared generic
  tone). An ordinal `ConfidenceBar` (100%-stacked, fixed order) summarizes a distribution —
  on the Thesis board (verdict confidence per holding) and the holding page's Facts section
  (fact confidence per company). `CitationLink` is already shared between Chat and the
  digest/report views.
- **IA & navigation** (Phase 9): five clusters (Portfolio, Research & Verification,
  Oversight, Config; Chat stays an ambient global slide-over, never a nav tab). The command
  palette's trailing label names the actual cluster instead of a blanket "Go to". A
  `StatusDot` primitive (severity/job-status, neutral-info-warning tone) appears on every
  severity-bearing list: Escalations, Activity's alerts and job log, the News feed, the
  per-holding news list, Disputed stories.
- **Motion & accessibility** (Phase 10): panel transitions (220ms ease-out,
  `prefers-reduced-motion` disables everything), an automated WCAG contrast audit (one real
  failure found and fixed — Unconfirmed was 2.86:1, now 3.76:1), and a colourblind-alternate
  toggle that adds pattern fills + underline styles to gain/loss, independent of light/dark.

**What this means for Ledger II:** the *foundation* (tokens, motion, a11y, the confidence
chip system, citations) is solid and should not be re-litigated. What's still true, and is
this proposal's actual subject, is something the first proposal's own research already
flagged and didn't fully solve: **Ledger's confidence system is "chips + citations."** That
was correct for where the app was. The research below asks what a *more* evidence-forward
product looks like once chips-and-citations is the baseline, not the ambition.

---

## 2. Research

Same method as the first proposal: named apps, concrete patterns, and an explicit read on
whether each one fits or fights StewardAI's "verification, not vibes" identity. Full
citations in [Sources](#8-sources).

### 2.1 Modern fintech / consumer investment apps

**Robinhood** (2025–2026 redesign) leans into glassy 3D ring charts and social-media-style
micro-interactions on every trade — explicitly engagement-bait, and the opposite of
evidence discipline; **not for StewardAI**. **Copilot Money** treats charts as the primary
design material on one deep, desaturated dark canvas with *fixed* per-account colors users
build "color literacy" around over time — the same principle behind Ledger's 8-slot
categorical palette, worth stating as an explicit rule rather than an implicit one.
**Monarch Money**'s dashboard is a stack of independently reorderable/removable widget
cards, with a sparkle-icon AI-insight affordance *anchored to the specific widget it
explains* rather than a floating chatbot — good pattern, with a real caution: full
composability can let a user hide the exact card (a Disputed-confidence item, a drift
alert) they should be forced to confront. **Betterment** interrupts with an estimated
tax-impact preview at the *moment* an allocation change would trigger one, not buried in
settings — "surface consequences right before the action" is directly reusable. **Cleo**'s
Roast/Hype-mode personality is fun for budgeting, actively wrong for a proof-standard tool.
**Sharesight** defaults its performance figures to dividend- and currency-adjusted total
return, never naive price return — "don't let the default number be the unqualified one"
maps directly onto StewardAI's ethos.

### 2.2 AI-native / AI-copilot product UI patterns

**Perplexity** popularized inline numbered citations with hover metadata (title, favicon,
snippet) at the exact clause they support, not a trailing source list — StewardAI's chat
already numbers citations per sentence (`chat-thread.tsx`'s `AnswerSentence`), so this is
mostly validation, with one gap: **Granola**-style citations show the literal quoted source
passage on hover, not just a link. **Notion AI** uses a selection-triggered contextual menu
("explain this," "rewrite") anchored at the point of selection, not a global chat box —
directly applicable to "select a number, ask about it in place." **Linear**'s Cmd+K is the
*primary* navigation method, at a very restrained visual spec — StewardAI's palette already
works this way; the open opportunity is depth (jump straight to "evidence for claim X"),
not existence. The "Landscape of Generative UI 2026" analysis names three tiers of how much
freedom an AI gets over its own rendered UI — **Chat Components** (fixed, developer-owned
card types), **Component Systems** (the AI emits structured data, a fixed renderer draws
it), **Embedded Generative UI** (full iframe hand-off, e.g. ChatGPT's Apps SDK or Claude's
own Artifacts). For a product whose entire premise is "every claim carries the same
confidence chrome," the middle tier is the only defensible default — freeform AI-rendered
HTML could omit a citation or a confidence chip by construction. Recent agentic-UX writing
converges on a **visible approval checkpoint** before any agent *acts* (what it's about to
do, why, an explicit approve/interrupt control) — StewardAI's chat `ActionCard`
(Confirm/Cancel, reasoning shown) already does this; it's confirmation the pattern is right,
and a constraint on anything added later. Memory-as-invisible-personalization (silently
shaping an answer from a remembered preference) is flagged as actively wrong for a
proof-standard tool — an unsighted input into a claim is a claim with a hidden citation.

### 2.3 Portfolio management / institutional / research-terminal tools

**Bloomberg Terminal**'s own accessibility work redesigned its red/green scheme for
deuteranopia specifically *because* "visual indicators could have large financial
repercussions" — direct external validation that Ledger's colourblind-alternate toggle is
core, not polish. **Morningstar** never blends "what we think the value is" with "how sure
we are" into one number: a separate, distinctly-labeled Uncertainty Rating *mechanically
widens the required margin of safety* before the opinion itself can move. **TIKR**
deliberately declines to collapse multi-factor analysis into one opaque score, showing the
standardized underlying data instead. **Koyfin** offers drag-resize, savable widget-pane
dashboards (watchlist + chart + news + claims as independent panes). **TradingView** ships
a discoverable `?`-triggered shortcut overlay and single-key, no-menu actions for the most
common cross-cutting task. None of this category conflicts with StewardAI's values — if
anything it's the closest match — with one honest gap: none of these terminal tools have a
real AI-copilot layer, so their patterns need combining with §2.2's, not taken alone.

### 2.4 Confidence and data quality as a first-class UI element (the central finding)

This is the one area worth building the whole proposal around, because it's the least
explored by Ledger so far and the most distinctive available to StewardAI:

1. **Decompose confidence into its own axis, not a blended score** (Morningstar). Ledger's
   four states already ARE a dedicated axis — the gap is that nothing *reads* that axis to
   change behavior downstream (an Unconfirmed claim renders identically to a Verified one
   everywhere except its own chip color).
2. **Classify how evidence relates to a claim, not just whether it exists** (scite.ai's
   Smart Citations: every citation is classified *supporting*, *contrasting*, or
   *mentioning*, shown as distinct icon+count badges, with a confidence percentage on the
   classification itself). This is close to a 1:1 structural analog for StewardAI's
   evidence links — right now a citation just links to a source; it doesn't say whether
   that source agrees, disagrees, or merely mentions the claim.
3. **Explain *why*, not just *what*, at the point of use** — a bare chip becomes wallpaper;
   "I'm unsure because X, here's how to check" is what actually helps.
4. **Guard against the wallpaper effect**: if most items are always "high confidence," a
   chip on every one trains users to stop reading it. Asymmetric visual weight — quiet by
   default, loud only for the states that need attention — keeps the signal meaningful.
5. **Provenance as its own tag, separate from confidence**: "verbatim from a filing" vs.
   "computed from stored figures" vs. "AI-inferred" answers a different question than "how
   sure are we this is true," and both deserve their own slot (Adobe's Content Authenticity
   Initiative and Google's SynthID Detector are the general 2026 direction for this).
6. **Graceful degradation over a confident-sounding wrong answer**: when the system's own
   uncertainty is high, fall back to showing raw data or an explicit "not enough evidence to
   answer this as a claim" response shape, rather than a fluent answer in the normal format.
7. **A visible threshold gate, not a continuous dial** (X's Community Notes: a note needs a
   helpfulness score ≥ 0.4 before it's shown at all) — worth the idea, with an explicit
   warning from the same research: undefined categorical reasons ("why is this Disputed")
   confuse people unless each one is precisely, visibly defined at the point it's shown.

---

## 3. Design principles, extended

The first proposal's six principles stand. Three more, specific to what §2.4 surfaces:

7. **Confidence gates behavior, not just color.** An Unconfirmed or Disputed claim should
   behave differently downstream (e.g. it cannot trigger an Oversight alert, or a report
   sentence built on it is visually distinguished) — not only look different.
8. **Evidence has a relationship to the claim, not just a link to it.** Every citation
   answers "does this source support, contradict, or merely mention the claim" as its own
   small piece of chrome, distinct from the claim's own overall confidence tier.
9. **Provenance and confidence are two different questions.** "How was this number
   produced" (verbatim / computed / AI-inferred) and "how sure are we it's right" each get
   their own slot; neither is inferred from the other.

---

## 4. Proposals

Each one names the research pattern it comes from, the StewardAI screen/data it attaches
to, and — critically — whether the data to back it honestly exists today or needs new
backend work (flagged explicitly, the same discipline the Phase 7 gap-closing work in this
session followed for the portfolio-history endpoint).

### 4.1 Evidence relationship badges (scite.ai, §2.4.2)

**Data needed:** new. `EvidenceLink`/`FactOut` currently carry a confidence tier but not a
*stance* toward the claim they support. Add a `relation: "supports" | "contradicts" |
"mentions"` field where a fact backs a KPI verdict or a thesis claim (verifier already
computes something adjacent to this when it flags a restated or conflicting figure —
`review.ts`'s reconciliation logic is the natural place to derive it, not a new model).

**UI:** a small icon+label badge next to each citation chip, in the same visual language as
`ConfidenceBadge` (icon + label + a fixed color per relation — reuse the diverging pair's
cool/warm logic: supports = cool, contradicts = warm, mentions = neutral — never a fifth
ad hoc hue). Shown inline in `CitationLink`'s hover preview and in the Evidence page.

### 4.2 "Why this confidence" expansion (agentic-design.ai, §2.4.3)

**Data needed:** partial. The validator and reconciliation logic already produce a specific
reason (`FailedCheck`, `status_reason` on facts/stories) — it exists, it's just not always
surfaced next to the chip. Wire the existing reason text into a one-click expansion on
`ConfidenceBadge` itself (a small disclosure, not a new modal — consistent with "citations
are a UI primitive, not a modal" from the first proposal), and where a concrete next action
exists (re-check on next 10-Q, add a second source), state it in the same expansion.

### 4.3 Asymmetric confidence weight (§2.4.4)

**Data needed:** none — this is a pure component change. `ConfidenceBadge` currently gives
Verified, Corroborated, Unconfirmed, and Disputed the same visual weight everywhere. Reduce
Verified's chrome (label only, no filled background, once it's been Verified for some
minimum age — e.g. unchanged across N sync cycles) so Unconfirmed/Disputed keep the eye's
attention instead of competing with a wall of equally-loud "fine" chips. This needs a small
new field or a client-side heuristic (age of the fact's last status change, already
timestamped) — no backend model change.

### 4.4 Provenance tag (§2.4.5, principle 9)

**Data needed:** small, mostly-already-there. `FactOut.origin` (`"extraction" | "owner"`)
and a fact's `value_as_written` vs. a KPI verdict's `rationale` already distinguish
"straight from a filing" from "computed." Surface this as a small, separate mono-font tag
(distinct from the confidence chip, per principle 9) wherever a figure is shown: "10-Q ·
verbatim", "computed", or — once any AI-inference step is introduced — "AI-inferred". No
existing screen currently states this explicitly; it's implied by context (a Fact vs. a
Verdict) but never labeled.

### 4.5 Graceful degradation in chat (§2.4.6)

**Data needed:** none new. `sentence_view`'s existing `no_source`/`unsupported` states
already partially do this (a wavy underline + "no source" chip). Extend the pattern one
level up: when an *entire answer* has too little evidence to state as a claim, the
assistant should not write a fluent paragraph and mark individual sentences unsupported
after the fact — it should return a distinctly-shaped "not enough evidence to answer this
as a claim; here's what I found" response (the raw facts/sources it did find, unformatted
as prose). This is a prompt/response-shape change in the chat backend, not a data-model
change.

### 4.6 Selection-triggered "ask about this" (Notion, §2.2)

**Data needed:** none. A text-selection or right-click affordance on any rendered figure or
claim (holding page's Facts table, Thesis board's KPI cells, a digest sentence) that opens
the existing chat slide-over pre-filled with a question about exactly that item — reusing
`openChatPanel()` (already exported from `chat-panel.tsx`) with an optional seed message.
Purely a frontend interaction change; no new endpoint.

### 4.7 Command palette: evidence-aware search (Linear, §2.2)

**Data needed:** none for facts/theses already loaded; would benefit from a lightweight
search endpoint for scale. Extend `filterCommands`/`baseCommands` (already covers every
page and every holding) to also resolve a typed metric or ticker directly to its KPI cell or
fact — "revenue growth AAPL" jumps straight to that KPI's verdict panel instead of only the
holding page. Start client-side (search within already-fetched holdings/theses data, same
as today's holding search); a dedicated backend search endpoint is a fallback only if
client-side matching proves too slow once thesis/fact counts grow.

### 4.8 Widget-composable Oversight workbench (Koyfin, §2.1/§2.3) — flagged, not committed

**Data needed:** none new, but real product-scope risk. A drag-resize, savable-layout
dashboard for Oversight (watchlist + drift chart + escalations + job log as independent
panes) is the single biggest lift in this document and the one item this proposal does
*not* recommend committing to yet — both because of its size and because of the research's
own caution: full composability must never let a user hide a safety-critical card
(Disputed-confidence items, drift-out-of-band alerts). If pursued, those specific cards
must be marked non-removable in the layout model from day one, not retrofitted.

---

## 5. Explicitly rejected

- **3D glass charts, haptic/social micro-interactions on price moves** (Robinhood) —
  engagement-bait; rewards reacting to noise, the opposite of evidence discipline.
- **Personality-driven copilot tone** (Cleo's Roast/Hype Mode) — StewardAI's copilot voice
  stays plain and citation-forward.
- **Freeform embedded generative UI as the default** for AI-rendered surfaces (ChatGPT Apps
  SDK / Claude Artifacts-style full iframe hand-off) — the Component Systems tier (§2.2) is
  the right default specifically so every AI-authored card is forced through the same
  confidence/citation chrome by construction; embedded generative UI stays available for
  genuinely bespoke visualizations (as the first proposal already scoped for visx), never
  for an AI-authored claim surface.
- **Invisible cross-session memory shaping an answer** — an unsighted input into a claim is
  a claim with a hidden citation; StewardAI's copilot should treat any remembered
  preference as just another citable fact used, never silent personalization.
- **Voice/camera-first interaction** (Gemini Live-style) — not this product's problem
  domain; a serious verification tool reads and writes dense text and numbers.
- **Collapsing confidence into one score** (rejecting the alternative to Morningstar's own
  approach) — TIKR's and Morningstar's shared lesson is to keep the decomposed reasons one
  click away, never to blend everything into a single opaque percentage.

---

## 6. Roadmap

Continuing the codebase's phase numbering (Ledger's first proposal used 6–10):

| Phase | Name | Scope | Touches |
|---|---|---|---|
| **11** | Evidence relationships | Add `relation` to evidence links (verifier/reconciliation-derived); relationship badges in `CitationLink` and the Evidence page. | Backend: `earnings/review.py`, `EvidenceLink`. Frontend: `citation.tsx`, evidence pages. |
| **12** | Confidence reasoning & weight | "Why this confidence" expansion on `ConfidenceBadge`; asymmetric visual weight for long-standing Verified items. | `confidence-badge.tsx`, `lib/evidence/confidence.ts`. |
| **13** | Provenance tags | A small mono-font provenance tag (verbatim/computed/AI-inferred) wherever a figure renders, derived from existing `FactOut.origin`/verdict rationale. | New `ProvenanceTag` component; wired into Facts tables, KPI cells, digest sentences. |
| **14** | Chat: graceful degradation & in-place questions | A distinct low-evidence response shape in chat; selection-triggered "ask about this" reusing `openChatPanel()`. | `chat/tools.py` (or wherever answer shaping lives), `chat-panel.tsx`, holding/thesis views. |
| **15** | Command palette depth | Resolve a typed metric/ticker directly to its KPI cell or fact, not just its holding page. | `lib/palette.ts`, `command-palette.tsx`. |

Phase 16 (the Oversight workbench, §4.8) is intentionally not scheduled — it needs a
separate scoping pass (layout persistence model, non-removable-card rule) before it becomes
phase work.

---

## 7. Success criteria

**Measurable:** every new relationship/provenance/reasoning surface reuses an existing
Ledger token and component pattern — no new ad hoc color, no new chip shape. Time-to-"why
should I trust this specific number" drops further: from "one tap to a citation" (Ledger I)
to "one tap to whether that citation supports or contradicts the claim, and why it's rated
the way it is" (Ledger II).

**Qualitative:** a screenshot of the Evidence page should read as "this system checked its
own work" before anyone reads a single figure. A low-confidence chat answer should look
*structurally* different from a confident one, not just differently colored. Nothing in
this document should make a screen feel busier — the goal is more truth per pixel already
on screen, not more pixels.

---

## 8. Sources

- [Design Compass — Robinhood 2026 redesign](https://designcompass.org/en/archive/robinhood-2026/)
- [Google Design — Robinhood investing, Material](https://design.google/library/robinhood-investing-material)
- [blakecrosley.com — Copilot Money design](https://blakecrosley.com/guides/design/copilot-money)
- [Monarch — AI in Monarch](https://help.monarch.com/hc/en-us/articles/37526856682260-AI-in-Monarch)
- [Wealthfront Blog — Introducing the new dashboard](https://www.wealthfront.com/blog/introducing-new-dashboard/)
- [Betterment — Portfolio rebalancing](https://www.betterment.com/help/portfolio-rebalancing)
- [datastudios.org — Cleo AI chat-based budgeting](https://www.datastudios.org/post/cleo-ai-chat-based-budgeting-cash-advances-and-pricing-explained)
- [BusinessWire — Interactive Brokers redesigns IBKR GlobalTrader](https://www.businesswire.com/news/home/20251218345494/en/Interactive-Brokers-Redesigns-IBKR-GlobalTrader-App-for-Simpler-Smarter-Mobile-Trading)
- [Sharesight — Investment portfolio performance](https://www.sharesight.com/investment-portfolio-performance/)
- [Unusual.ai — Perplexity platform guide, citation-forward design](https://www.unusual.ai/blog/perplexity-platform-guide-design-for-citation-forward-answers)
- [ShapeofAI — Citations pattern](https://www.shapeof.ai/patterns/citations)
- [dev.to — The landscape of Generative UI in 2026](https://dev.to/blove/the-landscape-of-generative-ui-in-2026-ad6)
- [Medium/Bootcamp — How Notion uses visual/perceptual design for AI feature adoption](https://medium.com/design-bootcamp/how-notion-utilize-visual-and-perceptual-design-principles-to-to-increase-new-ai-features-adoption-82e7f0dfcc4e)
- [getdesign.md — Linear.app design spec](https://getdesign.md/linear.app/design-md)
- [siit.io — Linear app review](https://www.siit.io/tools/trending/linear-app-review)
- [dev.to — Five UI/UX patterns for 2026 (agent checkpoints to spatial widgets)](https://dev.to/zdeb_fouc_f95089367652fc5/from-agent-checkpoints-to-spatial-widgets-five-uiux-patterns-that-look-poised-to-define-2026-m3i)
- [Hacker News — Bloomberg Terminal UI discussion](https://news.ycombinator.com/item?id=19153875)
- [lippihom.com — Designing for cognition: high-information-density interfaces](https://www.lippihom.com/blog/designing-for-cognition-the-enduring-value-of-high-information-density-interfaces)
- [Bloomberg UX — Designing the Terminal for colour accessibility](https://www.bloomberg.com/ux/2021/10/14/designing-the-terminal-for-color-accessibility/)
- [Morningstar — An introduction to the Morningstar Uncertainty Rating](https://www.morningstar.com/stocks/an-introduction-morningstar-uncertainty-rating)
- [Koyfin — Watchlists](https://www.koyfin.com/features/watchlists/)
- [traderhq.com — TIKR Terminal review](https://traderhq.com/tikr-terminal-review/)
- [TradingView — Shortcuts](https://www.tradingview.com/support/shortcuts/)
- [excavo.com — TradingView tips & tricks for power users](https://excavo.com/blog/tradingview-tips-tricks-power-users)
- [agentic-design.ai — Confidence visualization patterns](https://agentic-design.ai/patterns/ui-ux-patterns/confidence-visualization-patterns)
- [cocreate.consulting — Probabilistic UX design patterns](https://cocreate.consulting/field-notes/probabilistic-ux-design-patterns)
- [scite.ai — Features (Smart Citations)](https://scite.ai/features)
- [MIT Press — scite: a smart citation index](https://direct.mit.edu/qss/article/2/3/882/102990/scite-A-smart-citation-index-that-displays-the)
- [Sigma — Data lineage visualization tools](https://www.sigmacomputing.com/blog/data-lineage-visualization-tools)
- [Atlan — Data quality dashboards](https://atlan.com/know/data-quality-dashboards/)
- [Codecov docs — Pull request comments](https://docs.codecov.com/docs/pull-request-comments)
- [Codecov — Line-by-line coverage via GitHub Checks](https://about.codecov.io/blog/announcing-line-by-line-coverage-via-github-checks/)
- [Chalmers research — Community Notes helpfulness scoring](https://research.chalmers.se/publication/548591/file/548591_Fulltext.pdf)
- [Harvard Misinformation Review — Fact-checking at a crossroads](https://misinforeview.hks.harvard.edu/article/fact-checking-at-a-crossroads-fact-checkers-perspectives-on-community-notes-ai-integration-and-design-recommendations/)

Plus a direct read of the StewardAI repository as it stands after Ledger I's Phases 6–10 —
`tokens.css`, `steward/portfolio/history.py`, `confidence-badge.tsx`, `chat-thread.tsx`,
`earnings/review.py`, `palette.ts` — to ground every proposal above in what already exists
rather than a generic template.
