"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { CountryContextCard } from "@/components/macro/country-context-card";
import { CountrySlopegraph } from "@/components/macro/country-slopegraph";
import { ExposurePanel } from "@/components/macro/exposure-panel";
import { SeriesPanel } from "@/components/macro/series-panel";
import { TradeFlowPanel } from "@/components/macro/trade-flow-panel";
import { PageHeader } from "@/components/page-header";
import { PreviewBanner, Segmented } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { resourceData, useDataResource } from "@/lib/hooks/use-data-resource";
import { loadMacroPage } from "@/lib/macro/client";
import { commonTimeDomain, exposureSummary } from "@/lib/macro/logic";
import type { Vintage } from "@/lib/macro/types";

const VINTAGES = [
  { id: "latest", label: "Latest vintage" },
  { id: "first_release", label: "First release" },
] as const satisfies readonly { id: Vintage; label: string }[];

/**
 * Macro, financial cycle and trade (free-data plan, Phase 2): aligned small multiples with
 * revision markers and a first-release/latest toggle, a ranked country comparison, a
 * bilateral trade flow with concentration, and the owner-editable exposure mapping that links
 * all of it to one holding. Fixture-backed until the macro read APIs exist.
 */
export function MacroView() {
  const { state } = useDataResource(loadMacroPage);
  const data = resourceData(state);
  const [vintage, setVintage] = useState<Vintage>("latest");
  const domain = useMemo(() => (data ? commonTimeDomain(data.series) : null), [data]);
  const exposure = useMemo(() => (data ? exposureSummary(data.exposure) : null), [data]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Macro and trade"
        description="Country and sector context with release dates and revisions, tied to a holding only through a mapped, evidence-labelled exposure."
      />
      <PreviewBanner endpoint="GET /macro/series · /macro/comparison · /trade/slices · /holdings/{ticker}/exposure-map" />

      {state.kind === "loading" ? (
        <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading macro data…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title="Could not load macro data">{state.message}</AlertMessage> : null}

      {data && domain && exposure ? (
        <>
          <Blueprint as="section" aria-labelledby="macro-series-heading" className="flex flex-col gap-4 px-5 py-5">
            <PanelHeading
              kicker="Germany and the euro area"
              title={<span id="macro-series-heading">Indicators on one time axis</span>}
              action={<Segmented label="Vintage" options={VINTAGES} value={vintage} onChange={setVintage} />}
            />
            <p className="max-w-prose text-sm text-text-muted">
              Each panel keeps its own unit (never a shared axis across units) but shares the same time range, so a credit-cycle turn can be read against inflation and
              rates. Switch to <strong className="font-medium text-text">First release</strong> to see what was known when each value was first published; values
              with no archived first release stay empty. A revision never raises a new alert.
            </p>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {data.series.map((s) => (
                <SeriesPanel key={s.id} series={s} vintage={vintage} domain={domain} />
              ))}
            </div>
          </Blueprint>

          <CountrySlopegraph data={data.comparison} />

          <TradeFlowPanel slice={data.trade} />

          <ExposurePanel map={data.exposure} />

          <Blueprint as="section" aria-labelledby="macro-context-heading" className="flex flex-col gap-3 px-5 py-5">
            <PanelHeading
              kicker="Country context"
              title={<span id="macro-context-heading">What a country series means for {data.exposure.name}</span>}
            />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {data.series
                .filter((s) => s.id === "wb:DEU:gdp_growth" || s.id === "oecd:DEU:cpi")
                .map((s) => (
                  <CountryContextCard
                    key={s.id}
                    series={s}
                    holdingName={data.exposure.name}
                    exposure={exposure}
                    comparable={data.comparison.rows
                      .filter((r) => ["USA", "CZE", "JPN"].includes(r.country))
                      .map((r) => ({ countryName: r.countryName, value: r.to }))}
                  />
                ))}
            </div>
          </Blueprint>
        </>
      ) : null}
    </div>
  );
}
