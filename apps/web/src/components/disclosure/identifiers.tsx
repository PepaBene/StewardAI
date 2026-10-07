"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatScore, identifierTypeLabel, orderSuggestions, serverMessage, suggestionFlags, type IdentifierJob } from "@/lib/disclosure/operations";

/**
 * Looks up missing company and share-class identifiers and lists what it found. Every row is a
 * suggestion, not a confirmed identifier; this page can only show them, because the server has no
 * confirm or reject step yet.
 */
export function IdentifierSuggestions({ issuerId }: { issuerId?: number }) {
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<IdentifierJob | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/identifiers/resolve", { params: { query: issuerId != null ? { issuer_id: issuerId } : {} } });
      if (r.data) setJob(r.data);
      else setError(serverMessage(r.error, r.response.status, "Looking up identifiers"));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  const rows = job ? orderSuggestions(job.suggestions) : [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" onClick={() => void run()} disabled={busy}>
          {busy ? "Looking up…" : "Look up missing identifiers"}
        </Button>
        <p className="max-w-[72ch] text-xs text-text-muted">Searches public registries (GLEIF, OpenFIGI). Results are suggested, not confirmed, and nothing here confirms or rejects them yet.</p>
      </div>

      {error ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          {error}
        </p>
      ) : null}

      {job ? (
        <div className="space-y-3">
          <p role="status" className="border border-border bg-surface-muted px-3 py-2 text-sm">
            {job.issuers_checked} {job.issuers_checked === 1 ? "company" : "companies"} checked, {job.suggestions.length} {job.suggestions.length === 1 ? "suggestion" : "suggestions"}, {job.skipped.length} skipped, {job.failed.length} failed.
          </p>
          {rows.length === 0 ? (
            <p className="text-sm text-text-muted">No identifiers were suggested. Check the skipped and failed lists below, or run the lookup again after adding a company name.</p>
          ) : (
            <ul className="divide-y divide-border-soft border-y border-border-soft">
              {rows.map((s) => {
                const flags = suggestionFlags(s);
                return (
                  <li key={`${s.provider}-${s.identifier_type}-${s.identifier}-${s.issuer_id}`} className="space-y-1 py-3">
                    <p className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                      <span className="font-medium">{identifierTypeLabel(s.identifier_type)}</span>
                      <span className="num">{s.identifier}</span>
                      <span className="text-xs text-text-muted">suggested, not confirmed</span>
                    </p>
                    {flags.length ? (
                      <ul className="space-y-0.5">
                        {flags.map((fl) => (
                          <li key={fl} className="border border-warning-signal/40 bg-warning-signal-subtle px-2 py-0.5 text-sm font-medium">
                            {fl}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    <p className="text-xs text-text-muted">
                      Match score <span className="num">{formatScore(s.score)}</span> · from {s.provider} · company <span className="num">#{s.issuer_id}</span>
                      {s.security_id != null ? <> · security <span className="num">#{s.security_id}</span></> : null} · {s.stored ? "saved as a suggestion" : "not saved"}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
          <StringList title="Skipped" items={job.skipped} />
          <StringList title="Failed" items={job.failed} warn />
        </div>
      ) : null}
    </div>
  );
}

function StringList({ title, items, warn }: { title: string; items: readonly string[]; warn?: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className={`space-y-1 px-3 py-2 text-sm ${warn ? "border border-warning-signal/40 bg-warning-signal-subtle" : "border border-border bg-surface-muted"}`}>
      <p className="font-medium">{title}</p>
      <ul className="list-disc space-y-0.5 pl-4">
        {items.map((i) => (
          <li key={i}>{i}</li>
        ))}
      </ul>
    </div>
  );
}
