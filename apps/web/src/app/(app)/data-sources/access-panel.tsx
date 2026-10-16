"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { useMutation } from "@/components/reference/action-bits";
import { SaveNote } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { FRESHNESS_LABEL, needsAttention } from "@/lib/data-sources/data-sources";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import { label } from "@/lib/labels";
import { ageWords, plainProviderProblem } from "@/lib/oversight/plain";

const fetchAccess = () => getBrowserApiClient().GET("/sources/access", { params: { query: {} } });

/** One check row per endpoint, failing ones first; the raw HTTP status sits under Details. */
export function AccessPanel() {
  const { state, reload } = useApiResource(fetchAccess);
  // The endpoints that need a decision come first and alone; the working ones are one tick away.
  const [onlyAttention, setOnlyAttention] = useState(true);
  const now = useNow();
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => (data ? data.filter((a) => !onlyAttention || needsAttention(a)) : []), [data, onlyAttention]);

  return (
    <section id="access" aria-labelledby="access-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="access-heading" className="panel-title">
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
        <StatusList
          caption="Access state per provider endpoint"
          passWord="work without a problem"
          pageSize={10}
          rows={rows.map((a): StatusRow => {
            const problem = a.last_denial_at
              ? plainProviderProblem({ statusCode: a.last_denial_status_code, state: a.last_denial_state, detail: a.last_denial_detail })
              : null;
            const lastOk = a.last_success_at ? `Last worked ${ageWords(a.last_success_at, now)}` : "Has never worked";
            return {
              id: `${a.provider}/${a.endpoint}/${a.account_tier}`,
              state: needsAttention(a) ? "fail" : a.last_success_at ? "pass" : "unknown",
              lead: `${label("provider", a.provider)}, ${a.endpoint.replaceAll("_", " ")}`,
              sentence: a.hard_blocked ? (
                <>
                  Switched off after a refusal. {problem ? `${problem.sentence} ${problem.fix}` : "Reset it only if the plan or key changed."}
                </>
              ) : problem ? (
                <>
                  {problem.sentence} {lastOk}.
                </>
              ) : (
                <>
                  {lastOk}.{a.last_success_at ? ` ${FRESHNESS_LABEL[a.freshness]}.` : ""}
                </>
              ),
              details: (
                <>
                  <p>
                    Plan tier {a.account_tier} · state {a.state.replaceAll("_", " ")}
                    {a.last_success_at ? ` · last success ${formatDateTime(a.last_success_at)}` : ""}
                  </p>
                  {a.last_denial_at ? (
                    <p>
                      Last refusal {formatDateTime(a.last_denial_at)} · {problem?.raw}
                    </p>
                  ) : null}
                </>
              ),
              action: a.hard_blocked ? <ResetBlock provider={a.provider} endpoint={a.endpoint} onDone={reload} /> : undefined,
            };
          })}
        />
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
    <div role="group" aria-label={`Confirm reset of ${provider} ${endpoint}`} className="max-w-[26ch] space-y-1 rounded-control border border-dashed border-border p-2 text-xs font-normal">
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
