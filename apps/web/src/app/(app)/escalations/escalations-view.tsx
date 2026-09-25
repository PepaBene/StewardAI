"use client";

import { LoaderCircleIcon, RefreshCwIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { EscalationStatusBadge } from "@/components/news/escalation-status-badge";
import { ReportsTabs } from "@/components/news/reports-tabs";
import { SeverityBadge } from "@/components/news/severity-badge";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EscalationOut, type Holding } from "@/lib/api/client";
import { escalationHref, formatMovePct, parsePriceMove, thesisLink, TRIGGER_LABELS } from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: EscalationOut[] };

/** Impact notes on confirmed severity-2+ stories and large price moves, newest first. */
export function EscalationsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ticker = params.get("ticker");
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/escalations", { params: { query: { ticker: ticker ?? undefined, limit: 100 } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  const setTicker = (next: string | null) =>
    router.replace(next ? `${pathname}?ticker=${encodeURIComponent(next)}` : pathname, { scroll: false });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Impact notes: for each confirmed severity-2+ story and each large price move, what happened and which thesis
            KPIs or exit conditions it touches, or that none applies. Every sentence cites its sources.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          {load.kind === "ok" && load.items.length > 0 ? (
            <AsOfLabel at={load.items[0].created_at} kind="escalation" prefix="Latest note written" />
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>
      <ReportsTabs active="escalations" />

      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Holding</span>
          <select
            value={ticker ?? ""}
            onChange={(e) => setTicker(e.target.value || null)}
            className="rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text"
          >
            <option value="">All holdings</option>
            {ticker && !holdings.some((h) => h.ticker === ticker) ? <option value={ticker}>{ticker}</option> : null}
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name ?? h.ticker}
              </option>
            ))}
          </select>
        </label>
        {ticker ? (
          <Button variant="ghost" size="sm" onClick={() => setTicker(null)}>
            <XIcon aria-hidden="true" />
            All holdings
          </Button>
        ) : null}
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading impact notes…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the impact notes">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.items.length === 0 ? (
        <EmptyState title={ticker ? `No impact notes for ${ticker}` : "No impact notes yet"}>
          A note is written when a story of severity 2 or more is confirmed, or when a holding moves beyond the price-move
          threshold in Config: Event rules.
        </EmptyState>
      ) : null}

      {load.kind === "ok" && load.items.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised">
          {load.items.map((e) => (
            <EscalationRow key={e.id} escalation={e} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function EscalationRow({ escalation: e }: { escalation: EscalationOut }) {
  const link = thesisLink(e.body);
  const move = parsePriceMove(e.body.price_move);
  return (
    <li>
      <Link href={escalationHref(e.id)} className="block space-y-1.5 p-4 hover:bg-surface-muted">
        <span className="flex flex-wrap items-start justify-between gap-2">
          <span className="min-w-0 flex-1 font-medium">{e.body.headline || e.title}</span>
          <span className="flex items-center gap-1.5">
            {e.body.severity !== null ? <SeverityBadge severity={e.body.severity} /> : null}
            <EscalationStatusBadge body={e.body} />
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
          {e.ticker ? <span className="font-mono text-xs">{e.ticker}</span> : null}
          <span>{TRIGGER_LABELS[e.body.trigger]}</span>
          {e.body.trigger === "story" ? <span>{eventLabel(e.body.event_type)}</span> : null}
          {move ? <span className="num">{formatMovePct(move.movePct)} on {move.date}</span> : null}
          <span aria-hidden="true">·</span>
          <span>{link.kind === "none" ? link.text : `Touches ${[...link.kpis.map((k) => `KPI ${k.id}`), ...link.exitConditions.map(() => "an exit condition")].join(", ")}`}</span>
        </span>
        <AsOfLabel at={e.created_at} kind="escalation" prefix="Written" className="text-xs" />
      </Link>
    </li>
  );
}
