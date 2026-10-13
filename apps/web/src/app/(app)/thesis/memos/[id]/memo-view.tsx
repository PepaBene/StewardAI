"use client";

import { CheckIcon, LoaderCircleIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ThesisMemo } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

type Memo = { memo: { title: string; body: string }[]; stance_reason?: string; thesis?: Thesis; current_price?: number; currency?: string };
type Thesis = {
  why_owned?: string;
  kpis?: { id: string; metric: string; operator: string; threshold: number; unit: string; description?: string }[];
  exit_conditions?: string[];
  pre_mortem?: string[];
  conviction?: number | null;
  review_date?: string | null;
  price_target?: {
    currency: string;
    method: string;
    sell_target?: number | null;
    buy_target?: number | null;
    scenarios?: { bear: number; base: number; bull: number; p_bear: number; p_base: number; p_bull: number } | null;
  } | null;
};

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h3 className="text-sm font-semibold">{title}</h3>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </div>
  );
}

export function MemoView({ id }: { id: number }) {
  const [memo, setMemo] = useState<ThesisMemo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    getBrowserApiClient()
      .GET("/thesis/memos/{memo_id}", { params: { path: { memo_id: id } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setMemo(data);
        else setError(`The API answered HTTP ${response.status}.`);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [id, reloadKey]);

  async function decide(action: "accept" | "dismiss") {
    setBusy(true);
    setError(null);
    try {
      const path = { memo_id: id };
      const res =
        action === "accept"
          ? await getBrowserApiClient().POST("/thesis/memos/{memo_id}/accept", { params: { path } })
          : await getBrowserApiClient().POST("/thesis/memos/{memo_id}/dismiss", { params: { path } });
      if (res.data) setReloadKey((k) => k + 1);
      else setError(`Not done (HTTP ${res.response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!id) return <AlertMessage title="No such memo">The address has no memo number.</AlertMessage>;
  if (error && !memo) return <AlertMessage title="Could not load the memo">{error}</AlertMessage>;
  if (!memo)
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading…
      </p>
    );

  const body = memo.body as unknown as Memo;
  const thesis = body.thesis;
  const target = thesis?.price_target;
  const sc = target?.scenarios;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        kicker={memo.mode === "buy" ? "Thesis for a stock you might buy" : "Thesis for a holding"}
        title={`${memo.ticker}: ${memo.stance}`}
        description={`Written ${formatDateTime(memo.created_at)} by the app from a deep research run. It is a view to weigh, not an instruction; you decide.`}
        aside={
          memo.research_report_id ? (
            <Button asChild variant="outline" size="sm">
              <Link href={`/research/${memo.research_report_id}`}>The research report</Link>
            </Button>
          ) : undefined
        }
      />

      {error ? <AlertMessage title="Not done">{error}</AlertMessage> : null}

      {memo.mode === "buy" && memo.status === "open" ? (
        <Blueprint className="flex flex-wrap items-center gap-3 px-5 py-[14px]">
          <p className="text-sm">If you decide to buy {memo.ticker}, save this as its thesis so earnings checks and news links start from day one.</p>
          <Button size="sm" disabled={busy} onClick={() => void decide("accept")}>
            <CheckIcon aria-hidden="true" />
            Save as the thesis
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void decide("dismiss")}>
            <XIcon aria-hidden="true" />
            Dismiss
          </Button>
        </Blueprint>
      ) : null}
      {memo.mode === "buy" && memo.status !== "open" ? (
        <p className="text-sm text-text-muted">This memo is {memo.status}.</p>
      ) : null}

      <Blueprint aria-labelledby="summary-heading" className="flex flex-col gap-3 px-5 py-[18px]">
        <PanelHeading kicker="In short" title={<span id="summary-heading">Executive summary</span>} />
        <div className="max-w-[70ch] space-y-3 text-[15px] leading-relaxed whitespace-pre-line">{memo.executive_summary}</div>
        {body.stance_reason ? <p className="max-w-[70ch] text-sm text-text-muted">Why {memo.stance}: {body.stance_reason}</p> : null}
      </Blueprint>

      {thesis ? (
        <Blueprint aria-labelledby="numbers-heading" className="flex flex-col gap-4 px-5 py-[18px]">
          <PanelHeading kicker="What would prove or end it" title={<span id="numbers-heading">KPIs, exits and targets</span>} />
          {thesis.why_owned ? <p className="max-w-[70ch] text-sm">{thesis.why_owned}</p> : null}
          {sc && target ? (
            <p className="num text-sm">
              Bear {sc.bear} ({Math.round(sc.p_bear * 100)}%) · Base {sc.base} ({Math.round(sc.p_base * 100)}%) · Bull {sc.bull} (
              {Math.round(sc.p_bull * 100)}%) {target.currency}
              {target.sell_target ? ` · sell at ${target.sell_target}` : ""}
              {target.buy_target ? ` · add at or below ${target.buy_target}` : ""}
              <span className="block text-xs text-text-muted">Method: {target.method}</span>
            </p>
          ) : null}
          {thesis.kpis && thesis.kpis.length > 0 ? (
            <div>
              <h3 className="text-sm font-semibold">KPIs the earnings check will grade</h3>
              <ul className="mt-1 space-y-1 text-sm">
                {thesis.kpis.map((k) => (
                  <li key={k.id}>
                    <span className="font-mono text-xs">{k.metric}</span> {k.operator} {k.threshold} {k.unit}
                    {k.description ? <span className="text-text-muted"> · {k.description}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <List title="Exit conditions" items={thesis.exit_conditions ?? []} />
          <List title="Pre-mortem: the likeliest ways to lose 40%" items={thesis.pre_mortem ?? []} />
        </Blueprint>
      ) : null}

      {body.memo?.map((s) => (
        <Blueprint key={s.title} className="flex flex-col gap-2 px-5 py-[18px]">
          <h2 className="text-[19px] leading-tight font-semibold">{s.title}</h2>
          <div className="max-w-[70ch] space-y-3 text-[15px] leading-relaxed whitespace-pre-line">{s.body}</div>
        </Blueprint>
      ))}
      <p className="text-xs text-text-muted">
        Source ids in square brackets, like [S12], refer to the sources of the linked research report.
      </p>
    </div>
  );
}
