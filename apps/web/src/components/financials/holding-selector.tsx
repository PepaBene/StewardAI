import { displayTicker } from "@/lib/portfolio/holdings-view";

export type HoldingOption = { ticker: string; name: string | null };

/** The holdings of the latest snapshot (`GET /portfolio/holdings`) as a select. */
export function HoldingSelector({
  holdings,
  value,
  onChange,
  status,
}: {
  holdings: readonly HoldingOption[];
  value: string | null;
  onChange: (ticker: string) => void;
  status: { kind: "loading" } | { kind: "ok" } | { kind: "error"; title: string };
}) {
  const known = value !== null && holdings.some((h) => h.ticker === value);
  return (
    <label className="flex min-w-0 flex-col gap-0.5 text-xs text-text-muted">
      <span>Holding</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={holdings.length === 0} className="min-w-0 max-w-full border border-border bg-surface-raised px-2 py-1.5 text-sm text-text">
        {!known ? <option value="">{status.kind === "loading" ? "Loading holdings…" : holdings.length === 0 ? "No holdings" : "Choose a holding…"}</option> : null}
        {holdings.map((h) => (
          <option key={h.ticker} value={h.ticker}>
            {h.name ? `${h.name} (${displayTicker(h.ticker)})` : displayTicker(h.ticker)}
          </option>
        ))}
      </select>
      {status.kind === "error" ? <span role="alert">Holdings unavailable: {status.title}</span> : null}
    </label>
  );
}
