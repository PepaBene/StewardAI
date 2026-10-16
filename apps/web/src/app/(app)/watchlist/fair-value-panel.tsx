"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { StepSparkline } from "@/components/insight/step-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { money, pct } from "@/lib/insight/income";
import { num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

type WatchFairValue = components["schemas"]["WatchFairValue"];

const fetchFairValues = () => getBrowserApiClient().GET("/watchlist/fair-value");
const STORE = "watchlist-fair-value-basis";

/** The chosen basis per ticker is a per-viewer convenience: kept in this browser only. */
function loadChoices(): Record<string, string> {
  try {
    return JSON.parse(window.localStorage.getItem(STORE) ?? "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

/*
 * Watchlist fair-value column (insight I5.5): each idea's fair value on a basis chosen per item
 * (default: the latest saved scenario), its distance from the price, the recorded fair-value steps
 * and "Evaluate like a holding", which opens the company valuation workbench.
 */
export function WatchlistFairValue() {
  const { state } = useApiResource(fetchFairValues);
  const [choice, setChoice] = useState<Record<string, string>>({});
  useEffect(() => setChoice(loadChoices()), []);
  const choose = (ticker: string, basis: string) => {
    const next = { ...choice, [ticker]: basis };
    setChoice(next);
    try {
      window.localStorage.setItem(STORE, JSON.stringify(next));
    } catch {
      /* storage unavailable: the choice lasts for this visit */
    }
  };
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Price against fair value" title="Fair value of your ideas" />
      <div className="mt-3">
        <ResourceGate state={state} label="watchlist fair values">
          {(rows: WatchFairValue[]) =>
            rows.length === 0 ? (
              <p className="text-sm text-text-faint">No watchlist items.</p>
            ) : (
              <div className="overflow-x-auto border border-border">
                <table className="w-full text-sm">
                  <caption className="sr-only">Fair value of each watchlist idea on the chosen basis</caption>
                  <thead>
                    <tr className="bg-surface-muted text-xs text-text-muted">
                      <th scope="col" className="px-3 py-1.5 text-left font-medium">Idea</th>
                      <th scope="col" className="px-3 py-1.5 text-left font-medium">Basis</th>
                      <th scope="col" className="px-3 py-1.5 text-right font-medium">Price</th>
                      <th scope="col" className="px-3 py-1.5 text-right font-medium">Fair value</th>
                      <th scope="col" className="hidden px-3 py-1.5 text-left font-medium sm:table-cell">Recorded</th>
                      <th scope="col" className="px-3 py-1.5 text-right font-medium">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const selected = choice[r.ticker] ?? r.default_basis ?? r.bases[0]?.basis ?? null;
                      const b = r.bases.find((x) => x.basis === selected) ?? null;
                      const up = num(b?.upside);
                      const history = r.history.map((p) => ({ date: p.as_of, value: null, fair: num(p.value) }));
                      return (
                        <tr key={`${r.list_name}-${r.ticker}`} className="border-t border-border-soft align-top">
                          <th scope="row" className="px-3 py-1.5 text-left font-normal">
                            <span className="font-medium">{r.name ?? r.ticker}</span>
                            <span className="block text-xs text-text-muted">{r.list_name}</span>
                          </th>
                          <td className="px-3 py-1.5">
                            {r.bases.length ? (
                              <select
                                aria-label={`Fair-value basis for ${r.ticker}`}
                                value={selected ?? ""}
                                onChange={(e) => choose(r.ticker, e.target.value)}
                                className="max-w-48 border border-border bg-surface-raised px-1.5 py-1 text-xs"
                              >
                                {r.bases.map((x) => (
                                  <option key={x.basis} value={x.basis}>
                                    {x.label}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <span className="text-xs text-text-faint">No basis yet</span>
                            )}
                          </td>
                          <td className="num px-3 py-1.5 text-right">
                            {money(r.price, r.price_currency)}
                            {r.price_date ? <span className="block text-[11px] text-text-faint">{formatDay(r.price_date)}</span> : null}
                          </td>
                          <td className="num px-3 py-1.5 text-right">
                            {b ? (
                              <>
                                {money(b.value, b.currency)}
                                {up !== null ? (
                                  <span className={cn("block text-xs", up < 0 ? "text-loss" : "text-gain")}>
                                    {up < 0 ? `price ${pct(-up, 0)} above` : `price ${pct(up, 0)} below`}
                                  </span>
                                ) : null}
                              </>
                            ) : (
                              <span className="text-xs text-text-faint">—</span>
                            )}
                          </td>
                          <td className="hidden px-3 py-1.5 sm:table-cell">
                            <StepSparkline rows={history} label={`${r.ticker} recorded fair values`} />
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            <Link href={`/companies/${encodeURIComponent(r.ticker)}/valuation`} className="text-xs whitespace-nowrap text-accent-ink underline underline-offset-2">
                              Evaluate like a holding
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )
          }
        </ResourceGate>
      </div>
    </Blueprint>
  );
}
