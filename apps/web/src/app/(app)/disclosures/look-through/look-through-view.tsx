"use client";

import { useEffect, useState } from "react";

import { DealExposureTable, ExposureTable, RelationshipTable } from "@/components/disclosure/look-through";
import { Panel, SectionNav } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import { money } from "@/lib/disclosure/coverage";
import { coverageSentence, datingWarnings, weightText, type LookThrough } from "@/lib/disclosure/look-through";
import { dayOf, endOfDayMs } from "@/lib/disclosure/model";

const SECTIONS = [
  { id: "totals", label: "Totals" },
  { id: "exposure", label: "Exposure" },
  { id: "relationships", label: "Customers and suppliers" },
  { id: "deals", label: "Deals" },
] as const;

type Load = { kind: "loading" } | { kind: "ready"; view: LookThrough } | { kind: "error"; text: string };

/**
 * What the portfolio is exposed to through the companies and funds it holds: concentrated customers,
 * suppliers and deals, weighted by position. Totals are shares of the whole portfolio and an
 * unstated share stays unknown. A date control replays what was known on an earlier day.
 */
export function LookThroughPageView() {
  const [until] = useState(() => Date.now());
  const [cutoff, setCutoff] = useState(until);
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/portfolio/look-through", { params: { query: { as_of: new Date(cutoff).toISOString() } } });
        if (cancelled) return;
        if (r.data) setLoad({ kind: "ready", view: r.data });
        else setLoad({ kind: "error", text: r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `The look-through failed to load (HTTP ${r.response.status}).` });
      } catch (e) {
        if (!cancelled) setLoad({ kind: "error", text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cutoff]);

  const view = load.kind === "ready" ? load.view : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Portfolio look-through"
        description="Exposure to customers, suppliers and deals across what you hold, including the part reached through funds. Every share is of the whole portfolio."
        aside={view ? <span>Holdings snapshot {view.snapshot_date ?? "none"} · value {money(view.denominator_value)}</span> : undefined}
      />
      <label className="flex items-center gap-2 text-sm">
        <span className="font-medium">Show what was known on</span>
        <input
          type="date"
          value={dayOf(cutoff)}
          max={dayOf(until)}
          onChange={(e) => {
            const t = endOfDayMs(e.target.value);
            if (Number.isFinite(t)) {
              setLoad({ kind: "loading" });
              setCutoff(Math.min(t, until));
            }
          }}
          className="num h-9 border border-border bg-surface-raised px-2 text-sm outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
        />
      </label>

      {load.kind === "loading" ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {load.kind === "error" ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{load.text}</p> : null}

      {view ? (
        <>
          <SectionNav sections={SECTIONS} />
          <Panel id="totals" title="Totals" description={coverageSentence(view.totals, view.holdings)}>
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <Total label="Customer share known" value={weightText(view.totals.customer_known_weight)} />
              <Total label="In concentrated customers" value={weightText(view.totals.concentrated_customer_weight)} note={view.totals.concentrated_customer_holdings.join(", ")} />
              <Total label="Touched by an active deal" value={weightText(view.totals.deal_exposed_weight)} note={view.totals.deal_exposed_holdings.join(", ")} />
              <Total label="Reached through funds" value={weightText(view.totals.fund_look_through_weight)} />
              <Total label="Fund weight not mapped" value={weightText(view.totals.unmapped_fund_weight)} />
              <Total label="Largest weighted customer" value={weightText(view.totals.largest_customer_weighted, 2)} />
            </dl>
            {datingWarnings(view).length ? (
              <ul className="mt-2 space-y-0.5 text-xs text-text-muted">
                {datingWarnings(view).map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            ) : null}
          </Panel>
          <Panel id="exposure" title="Exposure" description="Each company's direct weight plus the weight reached through held funds, largest first.">
            <ExposureTable view={view} />
          </Panel>
          <Panel id="relationships" title="Customers and suppliers" description="Stated shares weighted by the holding. A record with no stated share is shown as unknown, not as zero.">
            <RelationshipTable view={view} />
          </Panel>
          <Panel id="deals" title="Deals" description="Deals known by the date for held companies.">
            <DealExposureTable view={view} />
          </Panel>
          <ul className="space-y-0.5 text-xs text-text-muted">
            {view.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
            <li>{view.method_version}</li>
          </ul>
        </>
      ) : null}
    </div>
  );
}

function Total({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num text-lg font-semibold">{value}</dd>
      {note ? <dd className="text-xs text-text-muted">{note}</dd> : null}
    </div>
  );
}
