"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Problem } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { evidenceMeter, type Scorecard, type ScoreSection } from "@/lib/journal/journal";
import { cn } from "@/lib/utils";

import { Notes } from "../../xray/shared";

const fetchScorecard = () => getBrowserApiClient().GET("/journal/scorecard", { params: { query: {} } });

export function ScorecardView() {
  const { state } = useApiResource(fetchScorecard);
  return (
    <ResourceGate state={state} label="the scorecard">
      {(card) => <Card card={card} />}
    </ResourceGate>
  );
}

const SHARE_LABEL: Record<string, string> = {
  sell_targets: "Reached",
  after_trim: "Fell afterwards",
  after_sell: "Fell afterwards",
};

function Card({ card }: { card: Scorecard }) {
  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-prose text-sm text-text-muted">
        {card.entries} decision{card.entries === 1 ? "" : "s"} from {formatDate(card.since)} to {formatDate(card.until)}. Squares count decisions toward the {card.min_decisions} a figure needs; with fewer, the section says so
        instead of reporting a number.
      </p>
      {(card.suggestions ?? []).length > 0 ? (
        <Blueprint className="border-l-2 border-accent p-4">
          <PanelHeading level={3} title="Worth a look" />
          <ul className="mt-2 list-disc pl-5 text-sm">
            {(card.suggestions ?? []).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </Blueprint>
      ) : null}
      <div className="grid gap-5 lg:grid-cols-2">
        {card.sections.map((section) => (
          <Section key={section.id} section={section} needed={card.min_decisions} />
        ))}
      </div>
      <Notes notes={[card.method]} title="How these are calculated" />
      <Downloads />
    </div>
  );
}

function save(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/** Private files for you: the report with its manifest of sources, coverage and versions, and the ledger. */
function Downloads() {
  const [busy, setBusy] = useState<"report" | "csv" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function get(kind: "report" | "csv") {
    setBusy(kind);
    setProblem(null);
    const client = getBrowserApiClient();
    try {
      if (kind === "report") {
        const res = await client.GET("/journal/export/report", { params: { query: {} } });
        if (!res.response.ok || !res.data) throw new Error("report");
        save(`owner-report-${res.data.manifest.generated_at.slice(0, 10)}.json`, "application/json", JSON.stringify(res.data, null, 2));
      } else {
        const res = await client.GET("/journal/export/entries.csv", { parseAs: "text" });
        if (!res.response.ok || typeof res.data !== "string") throw new Error("csv");
        save("decision-journal.csv", "text/csv", res.data);
      }
    } catch {
      setProblem("The file could not be created. Try again, or check that the API is running.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Export" title="Keep a copy" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        The report lists the sources it used, the dates, how much each part covers and the calculation versions. It is made for you when you ask and is stored nowhere.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy !== null} onClick={() => get("report")}>
          {busy === "report" ? "Preparing…" : "Download the report (JSON)"}
        </Button>
        <Button type="button" variant="outline" disabled={busy !== null} onClick={() => get("csv")}>
          {busy === "csv" ? "Preparing…" : "Download the journal (CSV)"}
        </Button>
      </div>
      <div className="mt-3">
        <Problem>{problem}</Problem>
      </div>
    </Blueprint>
  );
}

/** The one distinctive element: squares fill toward the minimum sample, so a thin section looks thin. */
function Meter({ n, needed }: { n: number; needed: number }) {
  const m = evidenceMeter(n, needed);
  return (
    <div className="flex items-center gap-2" role="img" aria-label={`${n} decisions; ${needed} needed for a figure`}>
      <div className="flex gap-0.5" aria-hidden="true">
        {Array.from({ length: m.needed }, (_, i) => (
          <span key={i} className={cn("size-2.5 border", i < m.filled ? (m.thin ? "border-warning-signal bg-warning-signal" : "border-accent bg-accent") : "border-border")} />
        ))}
      </div>
      <span className="num text-xs text-text-muted">{n}</span>
    </div>
  );
}

function Section({ section, needed }: { section: ScoreSection; needed: number }) {
  const unavailable = section.status === "unavailable";
  return (
    <Blueprint className={cn("p-4", unavailable && "border-dashed")}>
      <PanelHeading level={3} title={section.title} action={unavailable ? null : <Meter n={section.n ?? 0} needed={needed} />} />
      <p className={cn("mt-2 max-w-prose text-sm", section.status === "ok" ? "" : "text-text-muted")}>{unavailable ? `Not available: ${section.note}` : section.note}</p>
      {(section.rows ?? []).length > 0 ? (
        <table className="mt-3 w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-text-muted">
              <th scope="col" className="py-1 font-medium">
                Group
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Decisions
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Median change
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                {SHARE_LABEL[section.id] ?? "Rose"}
              </th>
            </tr>
          </thead>
          <tbody>
            {(section.rows ?? []).map((row) => (
              <tr key={row.label} className="border-t border-border-soft align-top">
                <th scope="row" className="py-1.5 text-left font-normal">
                  {row.label}
                  {row.detail ? <div className="text-xs text-text-muted">{row.detail}</div> : null}
                </th>
                <td className="num py-1.5 text-right">{row.n}</td>
                <td className="num py-1.5 text-right">{row.median_change === null || row.median_change === undefined ? "—" : formatPercent(row.median_change, { decimals: 1 })}</td>
                <td className="num py-1.5 text-right">{row.share === null || row.share === undefined ? "—" : formatPercent(row.share, { decimals: 0 })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Blueprint>
  );
}
