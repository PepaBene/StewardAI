"use client";

import { useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import {
  CONFIDENCE_LABEL,
  EVENT_DIRECTIONS,
  EVENT_STATES,
  pathLabel,
  SEVERITY_LABEL,
  type EventImpactData,
} from "@/lib/xray/owner-input";

import { Notes, TD, TH, TH_LEFT } from "../shared";
import { BUTTON, FIELD } from "./edge-form";

/** Follow an event at one company along the links above to what the portfolio holds, with each
 * link's source and confidence. A path is only as confident as its weakest link. */
export function EventPaths() {
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState<"downstream" | "upstream">("downstream");
  const [state, setState] = useState<"confirmed" | "reported" | "rumoured">("reported");
  const [impact, setImpact] = useState<EventImpactData | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().GET("/portfolio/xray/event-paths", {
      params: { query: { symbol: symbol.trim(), direction, state } },
    });
    setBusy(false);
    if (!res.response.ok || !res.data) {
      setImpact(null);
      return setProblem("The paths could not be worked out. Check the ticker and that the API is running.");
    }
    setImpact(res.data);
  }

  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Where an event reaches you" title="Follow an event along the links" />
      <form
        className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <label className="text-sm">
          <span className="mb-1 block font-medium">Company in the news</span>
          <input required className={FIELD} value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="TSM" maxLength={20} />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">What happened</span>
          <select className={FIELD} value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)}>
            {EVENT_DIRECTIONS.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">How sure is the news</span>
          <select className={FIELD} value={state} onChange={(e) => setState(e.target.value as typeof state)}>
            {EVENT_STATES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-end">
          <button type="submit" disabled={busy} className={BUTTON}>
            {busy ? "Working…" : "Show the paths"}
          </button>
        </div>
      </form>
      {problem ? (
        <p className="mt-2 text-sm text-loss" role="alert">
          {problem}
        </p>
      ) : null}
      {impact ? <Result impact={impact} /> : null}
    </Blueprint>
  );
}

function Result({ impact }: { impact: EventImpactData }) {
  return (
    <div className="mt-4 flex flex-col gap-3">
      <p className="max-w-prose text-sm">
        {impact.paths.length === 0
          ? `No held company is reached from ${impact.symbol}.`
          : `${impact.paths.length} path${impact.paths.length === 1 ? "" : "s"} from ${impact.symbol} reach ${formatPercent(impact.reached_weight, { decimals: 1 })} of the portfolio, counting each company once. ${impact.symbol} itself is ${formatPercent(impact.own_weight, { decimals: 1 })}.`}
      </p>
      {impact.paths.length > 0 ? (
        <div className="overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Paths from {impact.symbol} to companies you hold, with confidence and severity</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Path</th>
                <th scope="col" className={TH_LEFT}>Confidence</th>
                <th scope="col" className={TH}>Held</th>
                <th scope="col" className={TH}>Share of supplier revenue</th>
                <th scope="col" className={TH_LEFT}>Severity</th>
                <th scope="col" className={TH_LEFT}>Indicators to watch</th>
              </tr>
            </thead>
            <tbody>
              {impact.paths.map((p) => (
                <tr key={pathLabel(p)} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {pathLabel(p)}
                    <ul className="mt-1 space-y-0.5 text-xs text-text-muted">
                      {p.edges.map((e) => (
                        <li key={`${e.supplier}>${e.customer}`}>
                          {e.supplier} supplies {e.customer} ({e.product}), as of {formatDate(e.as_of)}: {e.why}.{" "}
                          <a href={e.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                            Source
                          </a>
                        </li>
                      ))}
                    </ul>
                  </th>
                  <td className="px-3 py-1.5">{CONFIDENCE_LABEL[p.confidence]}</td>
                  <td className={TD}>{formatPercent(p.weight, { decimals: 1 })}</td>
                  <td className={TD}>{p.revenue_share === null || p.revenue_share === undefined ? "Not stated" : formatPercent(p.revenue_share, { decimals: 0 })}</td>
                  <td className="px-3 py-1.5">{SEVERITY_LABEL[p.severity]}</td>
                  <td className="px-3 py-1.5 text-xs">{p.indicators.length > 0 ? p.indicators.join("; ") : "No thesis indicators"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Notes notes={[...impact.notes, impact.method]} title="How the paths are rated" />
    </div>
  );
}
