"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import type { PositionRiskPack } from "@/lib/risk/validation";

/**
 * "Risk of this holding" on a thesis card: its share of the portfolio's risk, who it moves with,
 * its part of the bad weeks and the worst past episodes. The pack is fetched when the section is
 * opened, so the board does not load one per card.
 */
export function PositionRisk({ ticker }: { ticker: string }) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="group border-t border-border-soft px-[18px] py-2 text-xs"
      onToggle={(e) => setOpened((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-text-muted select-none hover:text-text">Risk of this holding</summary>
      {opened ? <Pack ticker={ticker} /> : null}
    </details>
  );
}

function Pack({ ticker }: { ticker: string }) {
  const { state } = useApiResource(() =>
    getBrowserApiClient().GET("/portfolio/risk/position", { params: { query: { ticker } } }),
  );
  return (
    <div className="mt-2">
      <ResourceGate state={state} label="the risk of this holding">
        {(p: PositionRiskPack) => (p.available ? <Body p={p} /> : <p className="text-text-muted">{p.reason}</p>)}
      </ResourceGate>
    </div>
  );
}

function Body({ p }: { p: PositionRiskPack }) {
  const cluster = p.cluster ?? [];
  const stress = p.stress ?? [];
  return (
    <div className="space-y-2">
      {p.summary ? <p className="text-text">{p.summary}</p> : null}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
        <Stat label="Share of portfolio risk" value={formatPercent(p.risk_share)} />
        <Stat label="Risk per unit of size" value={p.risk_to_weight != null ? `${p.risk_to_weight.toFixed(2)}×` : "—"} />
        <Stat label="Own volatility a year" value={formatPercent(p.standalone_volatility)} />
        <Stat
          label="Part of the bad weeks"
          value={p.expected_shortfall_contribution != null ? formatPercent(p.expected_shortfall_contribution, { decimals: 0 }) : "—"}
        />
      </dl>
      {cluster.length > 0 ? (
        <p className="text-text-muted">
          Moves with {cluster.map((m) => `${m.ticker} (${m.correlation.toFixed(2)})`).join(", ")}.
        </p>
      ) : null}
      {stress.length > 0 ? (
        <ul className="text-text-muted">
          {stress.map((s) => (
            <li key={s.episode} className="num">
              {s.episode}: {Math.round(s.pnl).toLocaleString("en")}
              {s.basis === "proxied" ? " (estimated through a proxy)" : ""}
            </li>
          ))}
        </ul>
      ) : null}
      <ul className="list-disc pl-4 text-text-muted">
        {(p.notes ?? []).map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-text-muted">{label}</dt>
      <dd className="num font-semibold">{value}</dd>
    </div>
  );
}
