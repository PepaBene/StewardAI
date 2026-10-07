"use client";

import { useCallback, useEffect, useState } from "react";

import { callApi } from "@/app/(app)/relationships/decision-form";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/disclosure/model";
import { gateProgress, outcomeText, reviewOrder, type PairReviews } from "@/lib/disclosure/pair-reviews";

/**
 * Check whether the code handled each pair of consensus files correctly: a number that follows from
 * the two files, or a refusal to compare with a reason that is true. Labels are kept; the latest one
 * for a pair counts toward the gate that switches revision alerts on.
 */
export function PairReviewPanel({ ticker, onChanged }: { ticker: string; onChanged?: () => void }) {
  const [data, setData] = useState<PairReviews | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/estimates/pair-reviews", { params: { query: { ticker } } });
      if (r.data) setData(r.data);
      else setMessage({ ok: false, text: r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `The pair list failed to load (HTTP ${r.response.status}).` });
    } catch (e) {
      setMessage({ ok: false, text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
    }
  }, [ticker]);

  useEffect(() => {
    // The loader sets state only after the request returns, not during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function label(earlier: number, later: number, correct: boolean) {
    const key = `${earlier}:${later}`;
    setBusy(key);
    const r = await callApi(() => getBrowserApiClient().POST("/disclosures/estimates/pair-reviews", { body: { earlier_value_id: earlier, later_value_id: later, correct } }), correct ? "Marked correct." : "Marked wrong.");
    setMessage(r);
    setBusy(null);
    if (r.ok) {
      await load();
      onChanged?.();
    }
  }

  if (data === null) return message ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{message.text}</p> : <p className="text-sm text-text-muted">Loading the pairs…</p>;
  const progress = gateProgress(data.revision_gate);
  const pairs = reviewOrder(data.pairs);

  return (
    <div className="space-y-3">
      <p role="status" className="border border-dashed border-border bg-surface-muted px-3 py-2 text-sm">{progress.text}</p>
      {message ? <p role={message.ok ? "status" : "alert"} className={message.ok ? "border border-border bg-surface-muted px-3 py-2 text-sm" : "border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm"}>{message.text}</p> : null}
      {pairs.length === 0 ? (
        <p className="text-sm text-text-muted">This holding has fewer than two consensus files for any metric, so there is no pair to review.</p>
      ) : (
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {pairs.map((p) => {
            const key = `${p.earlier_id}:${p.later_id}`;
            return (
              <li key={key} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div className="min-w-0 max-w-[68ch] space-y-0.5 text-sm">
                  <p className="font-medium">
                    {p.metric}
                    {p.segment ? `, ${p.segment}` : ""}, {p.fiscal_period}
                  </p>
                  <p className="text-xs text-text-muted">
                    File known <span className="num">{formatDay(p.earlier_known_at)}</span> and file known <span className="num">{formatDay(p.later_known_at)}</span>
                  </p>
                  <p>{outcomeText(p)}</p>
                  {p.reviewed ? <p className="text-xs text-text-muted">Your label: {p.correct ? "correct" : "wrong"}{p.reviewed_at ? `, ${formatDay(p.reviewed_at)}` : ""}.</p> : null}
                </div>
                <div className="flex gap-2">
                  <Button type="button" size="sm" variant="outline" disabled={busy === key} onClick={() => void label(p.earlier_id, p.later_id, true)}>Correct</Button>
                  <Button type="button" size="sm" variant="outline" disabled={busy === key} onClick={() => void label(p.earlier_id, p.later_id, false)}>Wrong</Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
