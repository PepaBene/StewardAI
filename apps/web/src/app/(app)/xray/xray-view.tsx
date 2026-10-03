"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CoverageSection } from "@/components/xray/coverage-section";
import { ExposureSection } from "@/components/xray/exposure-section";
import { FundsSection } from "@/components/xray/funds-section";
import { InsiderSection } from "@/components/xray/insider-section";
import { OwnershipSection } from "@/components/xray/ownership-section";
import { ResourceShell } from "@/components/xray/resource-shell";
import { PageHeader } from "@/components/page-header";
import { Segmented, StatePlaceholder } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { holdingChoices, TABS, validAsOf, type TabId } from "@/lib/xray/view";

const TAB_IDS = TABS.map((t) => t.id) as readonly string[];

function setParam(key: string, value: string) {
  try {
    const url = new URL(window.location.href);
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
    window.history.replaceState(null, "", url);
  } catch {
    // A blocked history API only loses the shareable URL.
  }
}

/**
 * Funds and ownership (free-data plan, Phase 6): portfolio X-Ray with issuer, sector and
 * geography exposure, look-through flows, fund overlap and the coverage ledger; and, per
 * holding, institutional (13F), fund (N-PORT) and control (13D/G) ownership kept as separate
 * channels, with classified insider activity. Every figure comes from the API.
 */
export function XrayView() {
  const params = useSearchParams();
  const initialTab = params.get("tab");
  const [tab, setTab] = useState<TabId>(TAB_IDS.includes(initialTab ?? "") ? (initialTab as TabId) : "exposure");
  const [asOf, setAsOf] = useState(() => validAsOf(params.get("as_of")));
  const [pickedTicker, setPickedTicker] = useState(params.get("ticker") ?? "");

  const holdingsFetcher = useCallback(() => getBrowserApiClient().GET("/portfolio/holdings"), []);
  const holdings = useApiResource(holdingsFetcher);
  const choices = useMemo(
    () => (holdings.state.kind === "ok" ? holdingChoices(holdings.state.data.holdings) : holdings.state.kind === "error" && holdings.state.previous ? holdingChoices(holdings.state.previous.holdings) : null),
    [holdings.state],
  );
  const ticker = choices?.some((c) => c.ticker === pickedTicker) ? pickedTicker : (choices?.[0]?.ticker ?? "");

  useEffect(() => {
    setParam("tab", tab === "exposure" ? "" : tab);
  }, [tab]);
  const changeAsOf = (v: string) => {
    const valid = validAsOf(v);
    setAsOf(valid);
    setParam("as_of", valid);
  };
  const changeTicker = (v: string) => {
    setPickedTicker(v);
    setParam("ticker", v);
  };

  const perHolding = tab === "ownership" || tab === "insiders";

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Funds and ownership"
        description="What the portfolio really holds once funds are opened up, and who else holds each company, with every channel, date and gap kept separate."
      />
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <Segmented label="Section" options={TABS.map((t) => ({ id: t.id, label: t.label }))} value={tab} onChange={setTab} />
        {perHolding ? (
          <label className="flex min-w-0 items-center gap-2 text-sm">
            <span className="text-text-muted">Holding</span>
            <select
              value={ticker}
              onChange={(e) => changeTicker(e.target.value)}
              disabled={!choices || choices.length === 0}
              className="h-8 min-w-0 max-w-[16rem] border border-border bg-surface-raised px-2 text-sm text-text"
            >
              {(choices ?? []).map((c) => (
                <option key={c.ticker} value={c.ticker}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <p className="max-w-prose text-xs text-text-muted">{TABS.find((t) => t.id === tab)?.hint}</p>

      {tab === "exposure" ? <ExposureSection asOf={asOf} onAsOfChange={changeAsOf} /> : null}
      {tab === "funds" ? <FundsSection asOf={asOf} /> : null}
      {tab === "coverage" ? <CoverageSection /> : null}
      {perHolding ? (
        <ResourceShell state={holdings.state} label="Holdings">
          {() =>
            choices && choices.length > 0 && ticker ? (
              tab === "ownership" ? (
                <OwnershipSection key={ticker} ticker={ticker} />
              ) : (
                <InsiderSection key={ticker} ticker={ticker} />
              )
            ) : (
              <StatePlaceholder state="not_covered" reason="The portfolio has no directly held company to show ownership for (funds are not covered by 13F, 13D/G or Form 4)." />
            )
          }
        </ResourceShell>
      ) : null}
    </div>
  );
}
