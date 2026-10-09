"use client";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { basisLabel, basisSentence, episodeSpan, lossWidth, type ReplayEpisode, type ReplayPosition, type StressReplay } from "@/lib/risk/replay";
import { cn } from "@/lib/utils";

import { Notes, Warnings } from "../../xray/shared";

const fetchReplay = () => getBrowserApiClient().GET("/portfolio/risk/stress-replay");

const pct = (v: number | null | undefined, decimals = 1) => formatPercent(v, { decimals });

export function ReplayView() {
  const { state } = useApiResource(fetchReplay);
  return (
    <ResourceGate state={state} label="past episodes">
      {(report) => <Report report={report} />}
    </ResourceGate>
  );
}

function Report({ report }: { report: StressReplay }) {
  if (report.episodes.length === 0) {
    return (
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Past episodes" title="Nothing to replay yet" />
        <p className="mt-2 max-w-prose text-sm text-text-muted">{report.reason ?? "The portfolio has no positions."}</p>
      </Blueprint>
    );
  }
  const currency = report.base_currency ?? "CZK";
  const limit = report.drawdown_limit ?? null;
  const worst = Math.max(0.0001, ...report.episodes.map((e) => -(e.loss ?? 0)), limit ?? 0) * 1.1;
  return (
    <div className="flex flex-col gap-5">
      <Warnings warnings={report.warnings} />
      <Blueprint className="p-4">
        <PanelHeading
          level={3}
          kicker="Past episodes"
          title="What your holdings would have done in each one"
        />
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Today’s positions, moved by what prices did between the peak and the trough of each episode, in {currency}. Cash stays flat.
          {limit !== null ? ` The line marks your ${pct(limit, 0)} drawdown limit.` : ""}
        </p>
        <ol className="mt-3">
          {report.episodes.map((e) => (
            <EpisodeRow key={e.key} episode={e} currency={currency} worst={worst} limit={limit} />
          ))}
        </ol>
      </Blueprint>
      <Notes
        title="How this is calculated"
        notes={[
          report.method,
          `Method ${report.method_version}. Prices stored from ${report.history_start ?? "no date"}.`,
          "Peak and trough dates are approximate market dates, not official ones.",
        ]}
      />
    </div>
  );
}

function EpisodeRow({
  episode: e,
  currency,
  worst,
  limit,
}: {
  episode: ReplayEpisode;
  currency: string;
  worst: number;
  limit: number | null;
}) {
  const replayed = e.status === "replayed" && e.loss !== null && e.loss !== undefined;
  return (
    <li className="border-b border-border-soft last:border-b-0">
      <details className="group">
        <summary
          className={cn(
            "grid cursor-pointer list-none grid-cols-[minmax(0,14rem)_1fr_9rem] items-center gap-x-4 gap-y-1 py-3 max-md:grid-cols-[1fr_auto]",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden",
          )}
        >
          <div className="min-w-0">
            <div className="text-sm font-semibold">{e.label}</div>
            <div className="text-xs text-text-muted">{episodeSpan(e)}</div>
          </div>
          <div aria-hidden="true" className="relative h-3 bg-border-soft max-md:col-span-2 max-md:row-start-2">
            {replayed ? <div className="h-full bg-warning-signal" style={{ width: `${lossWidth(e.loss, worst)}%` }} /> : null}
            {limit !== null ? <div className="absolute -top-1 -bottom-1 w-px bg-text" style={{ left: `${lossWidth(-limit, worst)}%` }} /> : null}
          </div>
          <div className="num text-right text-sm">
            {replayed ? (
              <>
                <div className="font-semibold">{pct(e.loss, 1)}</div>
                <div className="text-xs text-text-muted">{formatMoney(e.pnl, currency, { decimals: 0, signed: true })}</div>
              </>
            ) : (
              <Absent reason={e.reason ?? "no data"} />
            )}
          </div>
        </summary>
        <EpisodeDetail episode={e} currency={currency} limit={limit} />
      </details>
    </li>
  );
}

function EpisodeDetail({ episode: e, currency, limit }: { episode: ReplayEpisode; currency: string; limit: number | null }) {
  if (e.status !== "replayed") {
    return <p className="pb-3 text-sm text-text-muted">{e.reason}</p>;
  }
  return (
    <div className="flex flex-col gap-3 pb-4">
      <p className="max-w-prose text-sm text-text-muted">{e.note}</p>
      <p className="text-sm">
        {basisSentence(e)}
        {e.breaches_limit && limit !== null ? <span className="text-warning-signal"> That reaches your {pct(limit, 0)} limit, which asks for a review.</span> : null}
      </p>
      {e.worst.length > 0 ? (
        <p className="text-sm">
          <span className="text-text-muted">Hardest hit: </span>
          {e.worst.map((p, i) => (
            <span key={p.ticker}>
              {i > 0 ? ", " : ""}
              <span className="font-semibold">{p.ticker}</span> <span className="num">{formatMoney(p.pnl, currency, { decimals: 0, signed: true })}</span>
            </span>
          ))}
          .
        </p>
      ) : null}
      <PositionTable positions={e.positions} currency={currency} />
    </div>
  );
}

function PositionTable({ positions, currency }: { positions: ReplayPosition[]; currency: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Each holding’s move in the episode and how it was found</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Size</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Move</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">{currency}</th>
            <th scope="col" className="py-1.5 font-medium">Found from</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.ticker} className="border-b border-border-soft last:border-b-0">
              <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{p.ticker}</th>
              <td className="num py-1.5 pr-3 text-right">{pct(p.weight)}</td>
              <td className="num py-1.5 pr-3 text-right">{p.move === null || p.move === undefined ? "—" : pct(p.move)}</td>
              <td className="num py-1.5 pr-3 text-right">{p.basis === "unavailable" ? "—" : formatMoney(p.pnl, currency, { decimals: 0, signed: true })}</td>
              <td className={cn("py-1.5", p.basis !== "real" && "text-text-muted")}>
                {basisLabel(p.basis)}
                {p.basis === "proxied" && p.proxy ? ` (${p.proxy}, beta ${p.beta?.toFixed(2)}, R² ${p.r_squared?.toFixed(2)})` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
