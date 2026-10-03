"use client";

import { useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";

type Probe = { id: string; label: string; run: (ticker: string) => Promise<{ ok: boolean; status: number; body: unknown }> };

const AV_STATEMENTS = [
  ["INCOME_STATEMENT", "Alpha Vantage income statement"],
  ["BALANCE_SHEET", "Alpha Vantage balance sheet"],
  ["CASH_FLOW", "Alpha Vantage cash flow"],
] as const;

const PROBES: readonly Probe[] = [
  ...AV_STATEMENTS.map(
    ([statement, label]): Probe => ({
      id: statement,
      label,
      run: async (ticker) => {
        const r = await getBrowserApiClient().POST("/holdings/{ticker}/financial-history/alpha-vantage/{statement}", { params: { path: { ticker, statement } } });
        return { ok: r.response.ok, status: r.response.status, body: r.data ?? r.error };
      },
    }),
  ),
  {
    id: "finnhub",
    label: "Finnhub peer candidates and EPS surprises",
    run: async (ticker) => {
      const r = await getBrowserApiClient().POST("/holdings/{ticker}/financial-history/finnhub-research", { params: { path: { ticker } } });
      return { ok: r.response.ok, status: r.response.status, body: r.data ?? r.error };
    },
  },
];

/** Scalar fields of an answer, as "key: value" (no raw JSON dump of unknown shapes). */
export function summarise(body: unknown): string[] {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return [];
  return Object.entries(body as Record<string, unknown>)
    .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    .map(([k, v]) => `${k}: ${String(v)}`);
}

/**
 * Owner-only probes that spend a free-tier request budget (Alpha Vantage, Finnhub). Nothing
 * runs on page load: each button asks for confirmation first, one call per confirmation,
 * and the outcome (including 409 / 422 / 429 refusals) is shown as the API states it.
 */
export function ProbePanel({ ticker, onDone }: { ticker: string; onDone: () => void }) {
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { ok: boolean; lines: string[] }>>({});

  async function run(probe: Probe) {
    setPending(null);
    setBusy(probe.id);
    try {
      const r = await probe.run(ticker);
      const detail = !r.ok && typeof r.body === "object" && r.body !== null && "detail" in r.body ? [String((r.body as { detail: unknown }).detail)] : summarise(r.body);
      setResults((prev) => ({ ...prev, [probe.id]: { ok: r.ok, lines: r.ok ? detail : [`HTTP ${r.status}`, ...detail] } }));
      if (r.ok) onDone();
    } catch (err) {
      setResults((prev) => ({ ...prev, [probe.id]: { ok: false, lines: [`Backend unreachable: ${err instanceof Error ? err.message : String(err)}`] } }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Blueprint as="section" aria-labelledby="probe-heading" className="flex min-w-0 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Owner action · spends free quota" title={<span id="probe-heading">Refresh from free sources</span>} />
      <p className="max-w-prose text-sm text-text-muted">
        Each button makes one request against a free-tier allowance for {ticker} (Alpha Vantage allows about 25 requests a day). Nothing runs automatically; a run needs an explicit confirmation, and a refusal (no key, no mapping, quota spent) is reported as is.
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {PROBES.map((p) => (
          <li key={p.id} className="flex min-w-0 flex-col gap-1 border border-border-soft p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 text-sm">{p.label}</span>
              {pending === p.id ? (
                <>
                  <span className="text-xs text-text-muted">Spend one free request?</span>
                  <button type="button" onClick={() => void run(p)} className="border border-border bg-accent px-3 py-1 text-xs font-semibold text-accent-foreground outline-hidden focus-visible:outline-2 focus-visible:outline-focus">
                    Confirm
                  </button>
                  <button type="button" onClick={() => setPending(null)} className="border border-border px-3 py-1 text-xs font-semibold outline-hidden hover:bg-border-soft focus-visible:outline-2 focus-visible:outline-focus">
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => setPending(p.id)}
                  className="border border-border px-3 py-1 text-xs font-semibold outline-hidden hover:bg-border-soft focus-visible:outline-2 focus-visible:outline-focus disabled:opacity-40"
                >
                  {busy === p.id ? "Running…" : "Run…"}
                </button>
              )}
            </div>
            {results[p.id] ? (
              <p role={results[p.id].ok ? "status" : "alert"} className="num text-xs text-text-muted">
                {results[p.id].ok ? "Done. " : "Not run. "}
                {results[p.id].lines.join(" · ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </Blueprint>
  );
}
