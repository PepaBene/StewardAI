"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { DEFECT_STATE_LABEL, FEATURE_CHOICES, type Defect } from "@/lib/release/release";

const fetchDefects = () => getBrowserApiClient().GET("/release/defects");

const FIELD =
  "h-9 w-full border border-border bg-surface px-2 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

/** Problems you find yourself, which no automatic check sees, ranked by the share of the portfolio they touch. */
export function DefectLog() {
  const { state, reload } = useApiResource(fetchDefects);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Problems you found" title="Defects, largest holding first" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        Log anything wrong that the data checks did not catch: a figure that disagrees with the source, a screen that misleads. Name the holding it affects so the list can be ranked by how much of the portfolio it touches.
      </p>
      <DefectForm onSaved={reload} />
      <ResourceGate state={state} label="the defect log">
        {(defects) => <DefectList defects={defects} onChanged={reload} />}
      </ResourceGate>
    </Blueprint>
  );
}

function DefectForm({ onSaved }: { onSaved: () => void }) {
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [ticker, setTicker] = useState("");
  const [feature, setFeature] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/release/defects", {
      body: { title: title.trim(), detail: detail.trim() || null, ticker: ticker.trim() || null, feature: feature || null },
    });
    setBusy(false);
    if (!res.response.ok) return setProblem(problemText(res.error, "The problem could not be logged."));
    setTitle("");
    setDetail("");
    setTicker("");
    setFeature("");
    onSaved();
  }

  return (
    <form
      className="mt-3 grid gap-3 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block font-medium">What is wrong</span>
        <input required className={FIELD} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} />
      </label>
      <label className="text-sm sm:col-span-2">
        <span className="mb-1 block font-medium">Details, such as what the screen showed and what the source says</span>
        <textarea className={`${FIELD} h-20 py-1`} value={detail} onChange={(e) => setDetail(e.target.value)} maxLength={2000} />
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Holding it affects</span>
        <input className={FIELD} value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="Optional, e.g. AAPL_US_EQ" maxLength={32} />
      </label>
      <label className="text-sm">
        <span className="mb-1 block font-medium">Feature</span>
        <select className={FIELD} value={feature} onChange={(e) => setFeature(e.target.value)}>
          <option value="">Not sure</option>
          {FEATURE_CHOICES.map((f) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
        <Button type="submit" disabled={busy}>
          {busy ? "Logging…" : "Log this problem"}
        </Button>
        <Problem>{problem}</Problem>
      </div>
    </form>
  );
}

function DefectList({ defects, onChanged }: { defects: Defect[]; onChanged: () => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  async function close(id: number, next: "fixed" | "wont_fix") {
    const res = await getBrowserApiClient().POST("/release/defects/{defect_id}", { params: { path: { defect_id: id } }, body: { state: next } });
    if (!res.response.ok) return setProblem(problemText(res.error, "That could not be saved."));
    setProblem(null);
    onChanged();
  }
  if (defects.length === 0) return <p className="mt-4 text-sm text-text-muted">Nothing logged yet.</p>;
  return (
    <div className="mt-4">
      <table className="w-full text-sm">
        <caption className="sr-only">Logged problems, open first, with the share of the portfolio each touches</caption>
        <thead>
          <tr className="text-left text-xs text-text-muted">
            <th scope="col" className="py-1 font-medium">
              Problem
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Portfolio affected
            </th>
            <th scope="col" className="py-1 font-medium">
              State
            </th>
          </tr>
        </thead>
        <tbody>
          {defects.map((d) => (
            <tr key={d.id} className="border-t border-border-soft align-top">
              <th scope="row" className="py-1.5 text-left font-normal">
                {d.title}
                <div className="text-xs text-text-muted">
                  {[d.ticker, d.feature, `logged ${formatDate(d.opened_at)}`].filter(Boolean).join(", ")}
                </div>
                {d.detail ? <div className="text-xs">{d.detail}</div> : null}
              </th>
              <td className="num py-1.5 text-right">{d.weight === null || d.weight === undefined ? "Not tied to a holding" : formatPercent(d.weight, { decimals: 1 })}</td>
              <td className="py-1.5">
                {d.state === "open" ? (
                  <span className="flex flex-wrap gap-2">
                    <button type="button" className="h-7 border border-border px-2 text-xs hover:bg-surface-muted" onClick={() => void close(d.id, "fixed")}>
                      Mark fixed
                    </button>
                    <button type="button" className="h-7 border border-border px-2 text-xs hover:bg-surface-muted" onClick={() => void close(d.id, "wont_fix")}>
                      Won&apos;t fix
                    </button>
                  </span>
                ) : (
                  DEFECT_STATE_LABEL[d.state]
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2">
        <Problem>{problem}</Problem>
      </div>
    </div>
  );
}
