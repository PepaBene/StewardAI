"use client";

import { useCallback, useMemo } from "react";

import { SrTable } from "@/components/charts";
import { PatentActions } from "@/components/reference/patent-actions";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { cellKey, cellSize, citationStep, parsePatentTrends } from "@/lib/graph/network";
import { useApiResource } from "@/lib/hooks/use-api-resource";

/** Shade steps for forward citations, lightest to darkest. Step 0 is "none recorded". */
const SHADE = ["bg-surface-muted", "bg-accent-200", "bg-accent-300", "bg-accent"] as const;
const SHADE_WORD = ["No citations", "Few citations", "Some citations", "Most citations"] as const;

/**
 * GET /holdings/{ticker}/patent-trends: patent families by technology class and priority year.
 * Square area is the number of families; shade is forward citations relative to the busiest
 * cell. Counts and citations are research signals, not a measure of patent value.
 */
export function PatentsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/patent-trends", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const m = useMemo(() => parsePatentTrends(raw), [raw]);

  return (
    <Blueprint aria-labelledby="patents-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Innovation · EPO patent families" title={<span id="patents-heading">Patents by technology</span>} />
      {state.kind === "loading" && !m ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !m ? (
        <p className="text-sm text-text-muted">
          Patent data unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {m && m.rows.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No patent families are attributed to this holding. A subsidiary&apos;s patents only count once a dated ownership
          link to it is confirmed, so this can be empty even for an active patenter.
        </p>
      ) : null}

      {m && m.rows.length > 0 ? (
        <>
          <p className="max-w-[68ch] text-sm text-text-muted">
            Square area shows how many patent families were first filed in that year; shade shows how often they are cited
            by later patents. One family can appear under several technology codes, so row totals can exceed the family
            count.
          </p>
          <div className="overflow-x-auto">
            <div
              aria-hidden="true"
              className="grid min-w-[34rem] items-center gap-px"
              style={{ gridTemplateColumns: `minmax(5rem,max-content) repeat(${m.years.length}, minmax(1.75rem,1fr))` }}
            >
              <span />
              {m.years.map((y) => (
                <span key={y} className="num text-center text-[11px] text-text-faint">
                  {String(y).slice(2)}
                </span>
              ))}
              {m.rows.map((cpc) => (
                <div key={cpc} className="contents">
                  <span className="pr-2 font-mono text-xs">{cpc}</span>
                  {m.years.map((y) => {
                    const c = m.cells.get(cellKey(cpc, y));
                    const size = c ? cellSize(c.families, m.maxFamilies) : 0;
                    return (
                      <span key={y} className="flex h-9 items-center justify-center border border-border-soft">
                        {c && size > 0 ? (
                          <span
                            title={`${cpc}, ${y}: ${c.families} families, ${c.citations} forward citations`}
                            className={SHADE[citationStep(c.citations, m.maxCitations)]}
                            style={{ width: `${size * 100}%`, height: `${size * 100}%`, maxWidth: 28, maxHeight: 28 }}
                          />
                        ) : null}
                      </span>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          <ul aria-label="Shade legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            {SHADE.map((s, i) => (
              <li key={s} className="flex items-center gap-1.5">
                <span aria-hidden="true" className={`inline-block size-3 border border-border ${s}`} />
                {SHADE_WORD[i]}
              </li>
            ))}
            <li>Blank cell: no families that year</li>
          </ul>
          <SrTable
            caption="Patent families by technology code and priority year"
            columns={["Technology code", "Year", "Families", "Forward citations", "Granted or active", "Expiring within 5 years"]}
            rows={m.rows.flatMap((cpc) =>
              m.years.flatMap((y) => {
                const c = m.cells.get(cellKey(cpc, y));
                return c ? [[cpc, y, c.families, formatNumber(c.citations), c.granted, c.expiringSoon]] : [];
              }),
            )}
          />
        </>
      ) : null}
      <PatentActions ticker={ticker} onChanged={reload} />
    </Blueprint>
  );
}
