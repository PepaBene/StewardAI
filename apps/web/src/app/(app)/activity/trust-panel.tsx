"use client";

import { LoaderCircleIcon, TriangleAlertIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatScore, sortTrust } from "@/lib/activity/activity";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchTrust = () => getBrowserApiClient().GET("/sources/trust");

/** Sources whose trust your feedback has moved, least trusted first. */
export function TrustPanel() {
  const { state } = useApiResource(fetchTrust);
  const rows = state.kind === "ok" ? sortTrust(state.data) : state.kind === "error" && state.previous ? sortTrust(state.previous) : null;
  return (
    <section id="source-trust" aria-labelledby="trust-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div>
        <h2 id="trust-heading" className="text-[22px] leading-tight font-semibold">
          Source trust
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          Every source starts at 1. Marking an item <em>wrong</em> lowers its source&rsquo;s score, <em>useful</em> raises it;
          below the threshold a source counts one tier lower in corroboration. Penalty, reward and threshold are in{" "}
          <Link href="/config/verification#trust-heading" className="text-accent-ink hover:underline">
            Config: Verification
          </Link>
          . Sources never judged are not listed.
        </p>
      </div>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading source trust…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load source trust: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {rows && rows.length === 0 ? <p className="text-sm text-text-muted">No feedback has moved any source yet.</p> : null}
      {rows && rows.length > 0 ? (
        <div className="overflow-x-auto border border-border">
          <table className="w-full min-w-[36rem] text-sm">
            <caption className="sr-only">Source trust scores, lowest first</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <th scope="col" className="px-3 py-2 text-left font-medium">Source</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Score</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Wrong</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Useful</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Standing</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.host} className="border-b border-border last:border-0">
                  <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-normal">
                    {r.host}
                  </th>
                  <td className="num px-3 py-2 text-right">{formatScore(r.score)}</td>
                  <td className="num px-3 py-2 text-right">{r.wrong_count}</td>
                  <td className="num px-3 py-2 text-right">{r.useful_count}</td>
                  <td className="px-3 py-2">
                    {r.demoted ? (
                      <span className="inline-flex items-center gap-1 rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 text-xs font-medium">
                        <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
                        Demoted: counts one tier lower
                      </span>
                    ) : (
                      <span className="text-xs text-text-muted">Normal tier</span>
                    )}
                  </td>
                  <td className="num px-3 py-2 text-xs text-text-muted">{formatDateTime(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
