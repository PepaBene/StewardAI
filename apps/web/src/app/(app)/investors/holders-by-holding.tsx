"use client";

import Link from "next/link";
import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { companyHref } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { breadthSentence, moveTone, moveWords, netHolders, orderMoves, shortManager } from "@/lib/investors/investors";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

type Holding = components["schemas"]["HoldingHolders"];
type Move = components["schemas"]["HolderMove"];

const COUNT = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

function money(value: number | string) {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(Number(value));
}

function quarter(period: string) {
  const [y, m] = period.split("-").map(Number);
  return `Q${Math.ceil(m / 3)} ${y}`;
}

/**
 * The page's opening view: each holding Form 13F covers, largest first, with how many filers own
 * it, how many came and went over the quarter (the bar), and what the tracked managers did.
 */
export function HoldersByHolding() {
  const fetcher = useMemo(() => () => getBrowserApiClient().GET("/investors/by-holding"), []);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const holdings = useMemo(() => data?.holdings ?? [], [data]);
  // One scale for every bar, so a long bar means many funds in absolute terms.
  const scale = useMemo(() => Math.max(1, ...holdings.flatMap((h) => [h.new_holders ?? 0, h.exited_holders ?? 0])), [holdings]);
  const sentence = breadthSentence(holdings);

  return (
    <section aria-labelledby="owners-heading" className="blueprint space-y-4 px-5 py-5">
      <div className="space-y-1">
        <h2 id="owners-heading" className="text-[22px] leading-tight font-semibold">
          Who owns what you own
        </h2>
        {data?.period ? (
          <p className="max-w-[72ch] text-sm text-text-muted">
            {`Positions at the end of ${quarter(data.period)}`}
            {data.previous_period ? `, against ${quarter(data.previous_period)}` : ""}
            {". "}
            {sentence ?? ""}
          </p>
        ) : null}
      </div>

      {state.kind === "loading" ? <p className="text-sm text-text-muted" role="status">Loading 13F holders…</p> : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load 13F holders: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {data && holdings.length === 0 ? <p className="text-sm text-text-muted">No 13F positions are stored for your holdings yet. Load new 13F filings to fill this view.</p> : null}

      {holdings.length > 0 ? (
        <>
          <div aria-hidden="true" className="hidden grid-cols-[minmax(9rem,13rem)_9rem_minmax(8rem,12rem)_1fr] gap-x-5 border-b border-border pb-1.5 text-xs text-text-muted md:grid">
            <span>Holding</span>
            <span className="text-right">Funds holding it</span>
            <span className="text-center">Closed · opened</span>
            <span>Tracked managers this quarter</span>
          </div>
          <ul className="divide-y divide-border">
            {holdings.map((h) => (
              <HoldingRow key={h.ticker} holding={h} scale={scale} />
            ))}
          </ul>
        </>
      ) : null}

      {data?.uncovered && data.uncovered.length > 0 ? (
        <p className="text-xs text-text-muted">
          {`Not covered by Form 13F (listed outside the US, or a European fund): ${data.uncovered.map(shortTicker).join(", ")}.`}
        </p>
      ) : null}
      <p className="text-xs text-text-muted">
        Funds are US managers above USD 100 million that file Form 13F. Changes compare share counts, so price moves do not count as buying or selling.
        {data?.period ? ` Quarter ended ${formatDate(data.period)}; filings are due 45 days later.` : ""}
      </p>
    </section>
  );
}

function HoldingRow({ holding: h, scale }: { holding: Holding; scale: number }) {
  const net = netHolders(h);
  const moves = orderMoves(h.tracked ?? []);
  const moved = moves.filter((m) => m.change !== "unchanged" && m.change !== "unknown");
  const still = moves.filter((m) => m.change === "unchanged");
  return (
    <li className="grid grid-cols-[1fr_auto] gap-x-5 gap-y-2 py-3 md:grid-cols-[minmax(9rem,13rem)_9rem_minmax(8rem,12rem)_1fr] md:items-center">
      <div className="min-w-0">
        <Link href={companyHref(h.ticker, "governance")} className="font-semibold hover:text-accent-ink hover:underline">
          {shortTicker(h.ticker)}
        </Link>
        <p className="truncate text-xs text-text-muted">
          {h.name ?? ""}
          {h.weight != null ? `${h.name ? ", " : ""}${(h.weight * 100).toFixed(1)}% of portfolio` : ""}
        </p>
      </div>
      <div className="text-right">
        <p className="num text-[15px] font-semibold">{COUNT.format(h.holders)}</p>
        <p className="num text-xs text-text-muted">
          {net === null ? money(h.value_usd) : `${net > 0 ? "+" : net < 0 ? "−" : "±"}${COUNT.format(Math.abs(net))} on the quarter`}
        </p>
      </div>
      <FlowBar opened={h.new_holders ?? null} closed={h.exited_holders ?? null} scale={scale} className="col-span-2 md:col-span-1" />
      <div className="col-span-2 flex flex-wrap items-center gap-1.5 md:col-span-1">
        {moved.map((m) => (
          <MoveChip key={m.cik} move={m} />
        ))}
        {still.length > 0 ? <span className="text-xs text-text-muted">{`${still.map((m) => shortManager(m.name)).join(", ")} unchanged`}</span> : null}
        {moves.length === 0 ? <span className="text-xs text-text-faint">None of the tracked managers holds it</span> : null}
      </div>
    </li>
  );
}

function MoveChip({ move }: { move: Move }) {
  const tone = moveTone(move.change);
  const shares = move.shares ?? move.previous_shares;
  return (
    <span
      title={shares != null ? `${COUNT.format(Number(shares))} shares${move.shares == null ? " before selling" : ""}` : undefined}
      className={cn(
        "inline-flex items-baseline gap-1 rounded-full px-2 py-0.5 text-xs",
        tone === "gain" ? "bg-gain-subtle text-gain" : tone === "loss" ? "bg-loss-subtle text-loss" : "bg-surface-muted text-text-muted",
      )}
    >
      <span className="font-medium text-text">{shortManager(move.name)}</span>
      {moveWords(move.change, move.share_change_pct)}
    </span>
  );
}

/** Filers that closed the position to the left of the centre line, those that opened one to the right. */
function FlowBar({ opened, closed, scale, className }: { opened: number | null; closed: number | null; scale: number; className?: string }) {
  if (opened === null || closed === null) return <span className={cn("text-center text-xs text-text-faint", className)}>No earlier quarter</span>;
  const pct = (n: number) => `${Math.max(n > 0 ? 1 : 0, (n / scale) * 50)}%`;
  return (
    <div className={cn("space-y-0.5", className)}>
      <div className="relative h-2.5" role="img" aria-label={`${COUNT.format(closed)} funds closed a position, ${COUNT.format(opened)} opened one`}>
        <div className="absolute inset-y-0 right-1/2 rounded-l-sm bg-loss/70" style={{ width: pct(closed) }} />
        <div className="absolute inset-y-0 left-1/2 rounded-r-sm bg-gain/70" style={{ width: pct(opened) }} />
        <div className="absolute -inset-y-0.5 left-1/2 w-px bg-text-faint" />
      </div>
      <div className="num flex justify-between text-[11px] text-text-faint">
        <span>{`−${COUNT.format(closed)}`}</span>
        <span>{`+${COUNT.format(opened)}`}</span>
      </div>
    </div>
  );
}
