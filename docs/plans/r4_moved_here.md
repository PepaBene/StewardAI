# R4 moved-here table (companies and funds)

The holding page (`/holdings/[ticker]`) is merged into the company page and now redirects to
`/companies/[ticker]` (`REDIRECTS` in `src/lib/nav.ts`). Every panel keeps its component; what
changed is the section it appears in. Holding-page panels render on the first route of their
section through `HoldingSectionPanels` in `app/(app)/holdings/[ticker]/holding-view.tsx`; the
ones below the fold sit in a closed `MoreDetail` block that mounts, and so fetches, only when opened.

Sections: 1 Overview, 2 Valuation, 3 Future, 4 Past, 5 Health, 6 Dividend, 7 Ownership and
management, 8 Research and thesis, 9 Network and reference.

## Holding page panels

| Panel (file) | Section | How it shows |
|---|---|---|
| HoldingHeader (holding-overview) | Sidebar and compact header of every section | Replaced by the EntityLayout sidebar: mark, name, price, fair value chip, position card |
| PositionPanel | 1 Overview | Shown when held |
| DrawdownPanel, NextReportPanel | 1 Overview | Visible |
| TimelinePanel ("What changed"), HoldingNews | 1 Overview | Visible |
| CandlePanel (intraday readout) | 1 Overview | "Price chart with intraday readout", opened on demand |
| PriceRiskSection | 1 Overview | "Price risk and volatility", opened on demand |
| ConcentrationPanel | 1 Overview | "Where it sits in your portfolio", opened on demand |
| ValuationPanel (compact), PeerPanel | 2 Valuation | "Multiples and peers as stored", opened on demand |
| EstimatesPanel | 3 Future | "Estimates as stored", opened on demand |
| SegmentsPanel, FinancialHistoryPanel | 4 Past | "Segments and financial history as stored", opened on demand |
| CashDebtPanel | 5 Health | "Cash and debt as stored", opened on demand |
| (none) | 6 Dividend | The holding page had no dividend panel |
| HoldersPanel | 7 Ownership and management | Visible |
| OwnershipSection, InsiderTradesSection, StakesPanel | 7 Ownership and management | "Ownership, insider trades and stakes", opened on demand |
| ScorePanel, KpiBarsPanel | 8 Research and thesis | Visible |
| Thesis and verdicts block (JobActions, KpiTable) | 8 Research and thesis | Visible; anchor `#thesis-heading` |
| FactsSection | 8 Research and thesis | Visible |
| HoldingResearch | 8 Research and thesis | Visible; anchor `#research` (target of "Start research") |
| WebFactsPanel, DisclosureAlertsPanel | 8 Research and thesis | "Web facts and disclosure alerts", opened on demand |
| NetworkPanel, PatentsPanel, PatentOverlapPanel, MacroContextPanel, TradePanel, InputCostsPanel | 9 Network and reference | "Network, patents and trade exposure", opened on demand |
| IdentitySection, PriceDataPanel, CorporateActionCoveragePanel, HoldingMorePanels | 9 Network and reference | "Reference data", opened on demand |

## The 16 company tabs

| Tab route | Section |
|---|---|
| (overview) | 1 Overview |
| valuation, consensus ("Analyst targets"), peers | 2 Valuation (sub-nav) |
| estimates | 3 Future |
| financials, segments | 4 Past (sub-nav) |
| ratios, allocation ("Capital allocation") | 5 Health (sub-nav) |
| dividends | 6 Dividend |
| governance | 7 Ownership and management |
| case, assessment, report | 8 Research and thesis (sub-nav) |
| network | 9 Network and reference |
| checks | No section; stays as the deep link for "See all checks" |

## Funds

Sections: Overview (was Profile), Holdings, Performance, Style (was Concentration and style),
Bond (was Bond characteristics), Checklist (was Due diligence). Same routes as before.

## Not carried over

- Valuation: analyst mean and thesis target are not yet in the fair-value strip (see report).
- Per-section "key figures" and the data-status footer are not built yet.
