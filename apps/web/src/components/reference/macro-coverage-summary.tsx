"use client";

import { StackedBar } from "@/components/charts";
import { AlertMessage } from "@/components/alert-message";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cellText } from "@/lib/reference/endpoint-forms";
import { num, recs } from "@/lib/parse-untyped";

const fetchCoverage = () => getBrowserApiClient().GET("/macro/coverage/portfolio");

/**
 * GET /macro/coverage/portfolio: how much of the portfolio's value has a mapped country or
 * product exposure. The rest is shown as unknown, never spread across countries.
 */
export function MacroCoverageSummary() {
  const { state } = useApiResource(fetchCoverage);
  const d = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const mapped = d ? num(d.mapped_weight) : null;
  const unknown = d ? num(d.unknown_weight) : null;
  const exposures = d ? recs(d.by_exposure) : [];
  const cols = exposures[0] ? Object.keys(exposures[0]).slice(0, 5) : [];

  return (
    <section aria-labelledby="macro-coverage-heading" className="blueprint space-y-3 px-5 py-5">
      <BlueprintCorners />
      <h2 id="macro-coverage-heading" className="text-[22px] leading-tight font-semibold">
        How much of the portfolio is mapped
      </h2>
      {state.kind === "loading" && !d ? <p className="text-sm text-text-muted">Loading coverage…</p> : null}
      {state.kind === "error" && !d ? <AlertMessage title={`Could not load coverage: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {d ? (
        <>
          <p className="max-w-[68ch] text-sm text-text-muted">
            As of {formatDate(d.as_of)}, in {d.base_currency}. Holdings without a mapped region or product are counted as unknown. Map them in the exposure
            editor on each holding page.
          </p>
          {mapped !== null && unknown !== null ? (
            <StackedBar
              title="Portfolio value by mapping"
              note={`${formatPct(mapped)} mapped`}
              segments={[
                { label: "Mapped", value: mapped },
                { label: "Unknown", value: unknown },
              ]}
            />
          ) : (
            <p className="text-sm text-text-muted">Mapped share unknown.</p>
          )}
          {exposures.length > 0 ? (
            <div className="relative overflow-x-auto border border-border">
              <table className="w-full min-w-[32rem] text-sm">
                <caption className="sr-only">Mapped exposure by place or product</caption>
                <thead className="bg-surface-muted">
                  <tr className="border-b border-border text-xs text-text-muted">
                    {cols.map((c) => (
                      <th key={c} scope="col" className="px-3 py-2 text-left font-medium">{c.replaceAll("_", " ")}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {exposures.map((r, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      {cols.map((c) => (
                        <td key={c} className="px-3 py-2">{cellText(r[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {d.gaps.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-text-muted">
              {d.gaps.map((g) => (
                <li key={g}>{g}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
