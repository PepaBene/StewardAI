"use client";

import { LoaderCircleIcon, TriangleAlertIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { useMutation } from "@/components/reference/action-bits";
import { SaveNote } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { FRESHNESS_LABEL, needsAttention } from "@/lib/data-sources/data-sources";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchAccess = () => getBrowserApiClient().GET("/sources/access", { params: { query: {} } });

/** One row per endpoint: tier, state, last success and last denial, filterable to the ones needing a decision. */
export function AccessPanel() {
  const { state, reload } = useApiResource(fetchAccess);
  const [onlyAttention, setOnlyAttention] = useState(false);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => (data ? data.filter((a) => !onlyAttention || needsAttention(a)) : []), [data, onlyAttention]);

  return (
    <section id="access" aria-labelledby="access-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="access-heading" className="text-[22px] leading-tight font-semibold">
            Endpoint access
          </h2>
          <p className="max-w-[68ch] text-sm text-text-muted">
            An endpoint is only used after a free-key test succeeds. A denied or paid-only endpoint is not retried until
            the configuration changes.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyAttention} onChange={(e) => setOnlyAttention(e.target.checked)} />
          Only endpoints that need attention
        </label>
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading access…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={`Could not load access: ${state.title}`}>{state.detail}</AlertMessage>
      ) : null}

      {data && rows.length === 0 ? (
        <EmptyState title={onlyAttention ? "Nothing needs attention" : "No endpoints recorded"}>
          {onlyAttention
            ? "Every recorded endpoint has succeeded and none was denied."
            : "Run the source audit to test each provider."}
        </EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <div className="relative overflow-x-auto border border-border">
          <table className="w-full min-w-[52rem] text-sm">
            <caption className="sr-only">Access state per provider endpoint</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">Endpoint</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Tier</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">State</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Last success</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Last denial</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={`${a.provider}/${a.endpoint}/${a.account_tier}`} className="border-b border-border align-top last:border-0">
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <span className="font-medium">{a.provider}</span>
                    <span className="block font-mono text-xs text-text-muted">{a.endpoint}</span>
                  </th>
                  <td className="px-3 py-2">{a.account_tier}</td>
                  <td className="px-3 py-2">
                    {a.hard_blocked ? (
                      <div className="space-y-1">
                        <span className="inline-flex items-center gap-1">
                          <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
                          Blocked
                        </span>
                        <ResetBlock provider={a.provider} endpoint={a.endpoint} onDone={reload} />
                      </div>
                    ) : (
                      a.state.replaceAll("_", " ")
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span className="num">{a.last_success_at ? formatDateTime(a.last_success_at) : "Never"}</span>
                    <span className="block text-xs text-text-muted">{FRESHNESS_LABEL[a.freshness]}</span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {a.last_denial_at ? (
                      <>
                        <span className="num">{formatDateTime(a.last_denial_at)}</span>
                        <span className="block text-text-muted">
                          {a.last_denial_state?.replaceAll("_", " ")}
                          {a.last_denial_status_code ? ` (HTTP ${a.last_denial_status_code})` : ""}
                        </span>
                        {a.last_denial_detail ? <span className="block text-text-muted">{a.last_denial_detail}</span> : null}
                      </>
                    ) : (
                      <span className="text-text-muted">None recorded</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/**
 * POST /provider-access/{provider}/{endpoint}/reset-block. Clears the block so the next audit may
 * try the endpoint again; that retry can spend free-tier credits, so it asks first.
 */
function ResetBlock({ provider, endpoint, onDone }: { provider: string; endpoint: string; onDone: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const { state, run } = useMutation();
  if (!confirm) {
    return (
      <div>
        <Button type="button" variant="outline" size="sm" onClick={() => setConfirm(true)}>
          Reset block
        </Button>
        <SaveNote state={state} />
      </div>
    );
  }
  return (
    <div role="group" aria-label={`Confirm reset of ${provider} ${endpoint}`} className="max-w-[26ch] space-y-1 border border-dashed border-border p-2 text-xs font-normal">
      <p>Only reset if the plan or key changed. The endpoint may be called again and spend free-tier credits.</p>
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          disabled={state.kind === "saving"}
          onClick={async () => {
            const ok = await run(
              () => getBrowserApiClient().POST("/provider-access/{provider}/{endpoint}/reset-block", { params: { path: { provider, endpoint } } }),
              () => "Block cleared.",
              onDone,
            );
            if (ok) setConfirm(false);
          }}
        >
          Reset
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => setConfirm(false)}>
          Keep blocked
        </Button>
      </div>
      <SaveNote state={state} />
    </div>
  );
}
