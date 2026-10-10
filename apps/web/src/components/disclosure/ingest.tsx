"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { ingestSummary, orderResults, serverMessage, type IngestResult } from "@/lib/disclosure/operations";

/**
 * Pulls the newest annual and quarterly filings for one company. The owner presses the button; nothing
 * runs on its own. Every filing is listed with its status, any reason and every named gap, and a server
 * refusal is shown in the server’s words.
 */
export function IngestFilings({ issuerId, issuerName, onDone }: { issuerId: number; issuerName: string; onDone?: () => void }) {
  const [since, setSince] = useState("");
  const [limit, setLimit] = useState(10);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/issuers/{issuer_id}/ingest", {
        params: { path: { issuer_id: issuerId }, query: { ...(since ? { since } : {}), limit } },
      });
      if (r.data) {
        setResult(r.data);
        onDone?.();
      } else setError(serverMessage(r.error, r.response.status, "Ingesting filings"));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const rows = result ? orderResults(result.results) : [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="font-medium">Filed on or after</span>
          <input type="date" value={since} onChange={(e) => setSince(e.target.value)} disabled={busy} className="h-9 border border-border bg-surface-raised px-2 text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Most filings to fetch (1 to 50)</span>
          <input
            type="number"
            min={1}
            max={50}
            value={limit}
            onChange={(e) => setLimit(Math.min(50, Math.max(1, Math.trunc(Number(e.target.value)) || 1)))}
            disabled={busy}
            className="num h-9 w-24 border border-border bg-surface-raised px-2 text-sm"
          />
        </label>
        <Button type="button" size="sm" onClick={() => void run()} disabled={busy}>
          {busy ? "Ingesting…" : "Ingest filings"}
        </Button>
      </div>
      <p className="max-w-[72ch] text-xs text-text-muted">
        Fetches, stores and parses the newest filings for {issuerName}. Filings already stored with identical content are skipped, and one that fails does not stop the rest.
      </p>

      {error ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="space-y-2">
          <p role="status" className="border border-border bg-surface-muted px-3 py-2 text-sm">
            {ingestSummary(result)}
          </p>
          {rows.length === 0 ? (
            <p className="text-sm text-text-muted">No filings matched. Try an earlier “Filed on or after” date or a larger limit.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <caption className="sr-only">Result for each filing: status, reason, quarantine, rows stored and gaps</caption>
                <thead>
                  <tr className="border-b border-border text-left">
                    <th scope="col" className="py-1.5 pr-3 font-medium">Accession</th>
                    <th scope="col" className="py-1.5 pr-3 font-medium">Status</th>
                    <th scope="col" className="py-1.5 pr-3 font-medium">Reason</th>
                    <th scope="col" className="py-1.5 pr-3 font-medium">Quarantined</th>
                    <th scope="col" className="py-1.5 pr-3 text-right font-medium">Facts mirrored</th>
                    <th scope="col" className="py-1.5 pr-3 text-right font-medium">Note rows</th>
                    <th scope="col" className="py-1.5 font-medium">Gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f) => (
                    <tr key={f.accession} className="border-b border-border-soft align-top">
                      <td className="num py-1.5 pr-3 whitespace-nowrap">{f.accession}</td>
                      <td className={`py-1.5 pr-3 ${f.status === "failed" ? "font-medium text-warning-signal" : ""}`}>{f.status.replaceAll("_", " ")}</td>
                      <td className="py-1.5 pr-3">{f.reason ?? <span className="text-text-muted">none given</span>}</td>
                      <td className={`py-1.5 pr-3 ${f.quarantined ? "font-medium text-warning-signal" : "text-text-muted"}`}>{f.quarantined ? "Yes, kept out of use" : "No"}</td>
                      <td className="num py-1.5 pr-3 text-right">{f.facts_mirrored}</td>
                      <td className="num py-1.5 pr-3 text-right">{f.note_rows}</td>
                      <td className="py-1.5">
                        {f.gaps.length === 0 ? (
                          <span className="text-text-muted">none named</span>
                        ) : (
                          <ul className="list-disc space-y-0.5 pl-4">
                            {f.gaps.map((g) => (
                              <li key={g}>{g}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
