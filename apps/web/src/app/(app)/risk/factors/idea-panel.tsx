"use client";

import { useState } from "react";

import { Field, Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import type { IdeaFitReport } from "@/lib/risk/sizing";

type Region = "developed" | "europe" | "us";

/** Which candidate ideas would pull the book’s biggest style tilt back toward the benchmark. A tie-breaker, never a reason to take a weaker idea. */
export function IdeaPanel({ region }: { region: Region }) {
  const [symbols, setSymbols] = useState("");
  const [weight, setWeight] = useState("5");
  const [report, setReport] = useState<IdeaFitReport | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    const list = symbols
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const pct = Number(weight.replace(",", "."));
    if (list.length === 0) return setProblem("Enter one or more price symbols, such as MSFT or ASML.");
    if (!Number.isFinite(pct) || pct < 0.5 || pct > 20) return setProblem("The weight must be between 0.5 and 20 percent.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().GET("/portfolio/risk/factors/ideas", {
      params: { query: { symbols: list.join(","), region, weight: pct / 100 } },
    });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The ideas could not be tested."));
    setReport(res.data);
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Ideas" title="Would an idea balance the tilt?" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        Adds each idea to a copy of today’s portfolio, paid for from cash, and shows how far your style tilts end up from the benchmark’s. An idea needs stored daily prices.
      </p>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <Field label="Symbols" className="w-72">
          <Input value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="MSFT, ASML" />
        </Field>
        <Field label="Weight (%)" className="w-28">
          <Input inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} />
        </Field>
        <Button type="submit" disabled={busy}>
          {busy ? "Testing…" : "Test the ideas"}
        </Button>
      </form>
      <div className="mt-3">
        <Problem>{problem}</Problem>
      </div>
      {report ? (
        report.reason ? (
          <p className="mt-4 text-sm text-text-muted">{report.reason}</p>
        ) : (
          <div className="mt-4 overflow-x-auto border-t border-border pt-4">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Ideas ranked by how much they reduce the style tilt</caption>
              <thead>
                <tr className="border-b border-border text-left">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Idea</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Distance from benchmark</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Relief</th>
                  <th scope="col" className="py-1.5 font-medium">Main tilt</th>
                </tr>
              </thead>
              <tbody>
                {report.ideas.map((i) => (
                  <tr key={i.symbol} className="border-b border-border-soft align-top last:border-b-0">
                    <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{i.symbol}</th>
                    {i.status === "ok" ? (
                      <>
                        <td className="num py-1.5 pr-3 text-right">
                          {i.imbalance_before?.toFixed(2)} → {i.imbalance_after?.toFixed(2)}
                        </td>
                        <td className="num py-1.5 pr-3 text-right font-semibold">{i.relief == null ? "—" : `${i.relief > 0 ? "+" : "−"}${Math.abs(i.relief).toFixed(2)}`}</td>
                        <td className="py-1.5 text-text-muted">
                          {i.dominant_label} {i.dominant_before?.toFixed(2)} → {i.dominant_after?.toFixed(2)}
                        </td>
                      </>
                    ) : (
                      <td colSpan={3} className="py-1.5 text-text-muted">{i.reason}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-text-muted">
              Held at {formatPercent(report.weight, { decimals: 1 })} of the portfolio. Positive relief means the idea moves the book toward the benchmark’s style; use it to choose between comparable ideas only.
            </p>
          </div>
        )
      ) : null}
    </Blueprint>
  );
}
