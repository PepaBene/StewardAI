"use client";

import { CircleCheckIcon, CircleDashedIcon, LoaderCircleIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { getBrowserApiClient } from "@/lib/api/client";
import { describeCorrected, EVAL_KIND_LABELS, evalTarget, EXPORT_COMMAND } from "@/lib/activity/activity";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchCandidates = () => getBrowserApiClient().GET("/eval-candidates");

/** Items marked wrong and added to an evaluation set, and whether they are in the repo yet. */
export function EvalCandidatesPanel() {
  const { state } = useApiResource(fetchCandidates);
  const rows = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const pending = rows?.filter((r) => !r.exported_at).length ?? 0;
  return (
    <section id="eval-candidates" aria-labelledby="eval-heading" className="scroll-mt-24 space-y-3">
      <div>
        <h2 id="eval-heading" className="text-[22px] leading-tight font-semibold">
          Evaluation candidates
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          A figure or news item you mark <em>wrong</em> can be added to the golden (earnings) or labelled (news) evaluation
          set with the right value or label. They reach the CI evals once{" "}
          <code className="rounded-sm bg-surface-muted px-1 font-mono text-xs text-text">{EXPORT_COMMAND}</code> (run in{" "}
          <code className="font-mono text-xs">apps/backend</code>) has written them into the repository and the change is
          committed.
        </p>
      </div>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading evaluation candidates…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load evaluation candidates: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {rows && rows.length === 0 ? <p className="text-sm text-text-muted">None yet.</p> : null}
      {rows && rows.length > 0 ? (
        <>
          <p className="text-sm" role="status">
            <span className="num font-medium">{pending}</span> waiting for export · <span className="num">{rows.length - pending}</span> exported
          </p>
          <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
            <table className="w-full min-w-[48rem] text-sm">
              <caption className="sr-only">Evaluation candidates, newest first</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Candidate</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Item</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Corrected</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Exported</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const target = evalTarget(c);
                  return (
                    <tr key={c.id} className="border-b border-border align-top last:border-0">
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        <span className="num font-medium">#{c.id}</span> {EVAL_KIND_LABELS[c.kind]}
                        <span className="num block text-xs text-text-muted">added {formatDateTime(c.created_at)}</span>
                      </th>
                      <td className="px-3 py-2">
                        {target.href ? (
                          <Link href={target.href} className="text-accent hover:underline">
                            {target.label}
                          </Link>
                        ) : (
                          target.label
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {describeCorrected(c)}
                        {c.note ? <span className="block text-xs text-text-muted">&ldquo;{c.note}&rdquo;</span> : null}
                      </td>
                      <td className="px-3 py-2">
                        {c.exported_at ? (
                          <span className="inline-flex flex-col">
                            <span className="inline-flex items-center gap-1">
                              <CircleCheckIcon aria-hidden="true" className="size-3.5 text-neutral-signal" />
                              Exported {formatDateTime(c.exported_at)}
                            </span>
                            {c.exported_to ? <span className="font-mono text-xs break-all text-text-muted">{c.exported_to}</span> : null}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-text">
                            <CircleDashedIcon aria-hidden="true" className="size-3.5 text-text-muted" />
                            Not exported yet
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
