"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  confirmAllowed,
  confirmNeedsNote,
  formatScore,
  identifierTypeLabel,
  MATCH_STATUS_LABEL,
  NOTE_MIN,
  orderSuggestions,
  serverMessage,
  suggestionFlags,
  type IdentifierJob,
  type MatchStatus,
  type StoredSuggestion,
} from "@/lib/disclosure/operations";

const STATUSES: MatchStatus[] = ["suggested", "confirmed", "rejected"];

/**
 * Looks up missing company and share-class identifiers and lists what was found. A suggestion stays a
 * suggestion until a person decides it. Decisions are final.
 */
export function IdentifierSuggestions({ issuerId }: { issuerId?: number }) {
  const [status, setStatus] = useState<MatchStatus>("suggested");
  const [rows, setRows] = useState<StoredSuggestion[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<IdentifierJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (s: MatchStatus) => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/identifiers/suggestions", { params: { query: { match_status: s } } });
      if (r.data) {
        setRows(r.data);
        setError(null);
      } else setError(serverMessage(r.error, r.response.status, "Loading suggestions"));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  useEffect(() => {
    // The loader sets state only after the request returns, not during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(status);
  }, [load, status]);

  async function run() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/identifiers/resolve", { params: { query: issuerId != null ? { issuer_id: issuerId } : {} } });
      if (r.data) {
        setJob(r.data);
        await load(status);
      } else setError(serverMessage(r.error, r.response.status, "Looking up identifiers"));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function decide(s: StoredSuggestion, decision: "confirmed" | "rejected", note: string) {
    setError(null);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/identifiers/suggestions/{suggestion_id}/decision", {
        params: { path: { suggestion_id: s.id } },
        body: { decision, ...(note.trim() ? { note: note.trim() } : {}) },
      });
      if (r.data) {
        setNotice(`${identifierTypeLabel(s.identifier_type)} ${s.identifier} ${decision === "confirmed" ? "confirmed" : "rejected"}.`);
        await load(status);
      } else setError(serverMessage(r.error, r.response.status, "Saving the decision"));
    } catch (e) {
      setError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const ordered = rows ? orderSuggestions(rows) : [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" onClick={() => void run()} disabled={busy}>
          {busy ? "Looking up…" : "Look up missing identifiers"}
        </Button>
        <p className="max-w-[72ch] text-xs text-text-muted">Searches public registries (GLEIF, OpenFIGI) and adds what it finds as suggestions. Nothing is confirmed until you decide it.</p>
      </div>

      <div role="group" aria-label="Which suggestions to show" className="flex flex-wrap gap-1">
        {STATUSES.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={s === status}
            onClick={() => {
              setStatus(s);
              setRows(null);
            }}
            className={`h-9 border px-3 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none ${s === status ? "border-accent bg-accent text-accent-contrast" : "border-border bg-surface-raised hover:bg-surface-muted"}`}
          >
            {MATCH_STATUS_LABEL[s]}
          </button>
        ))}
      </div>

      {error ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="border border-border bg-surface-muted px-3 py-2 text-sm">
          {notice}
        </p>
      ) : null}

      {job ? (
        <div className="space-y-2">
          <p role="status" className="border border-border bg-surface-muted px-3 py-2 text-sm">
            Last lookup: {job.issuers_checked} {job.issuers_checked === 1 ? "company" : "companies"} checked, {job.suggestions.length} {job.suggestions.length === 1 ? "suggestion" : "suggestions"}, {job.skipped.length} skipped, {job.failed.length} failed.
          </p>
          <StringList title="Skipped" items={job.skipped} />
          <StringList title="Failed" items={job.failed} warn />
        </div>
      ) : null}

      {rows === null ? (
        <p className="text-sm text-text-muted">Loading suggestions…</p>
      ) : ordered.length === 0 ? (
        <p className="text-sm text-text-muted">
          {status === "suggested" ? "No suggestions are waiting. Press “Look up missing identifiers” to search for more." : `No ${MATCH_STATUS_LABEL[status].toLowerCase()} suggestions yet.`}
        </p>
      ) : (
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {ordered.map((s) => (
            <SuggestionRow key={s.id} s={s} onDecide={decide} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SuggestionRow({ s, onDecide }: { s: StoredSuggestion; onDecide: (s: StoredSuggestion, d: "confirmed" | "rejected", note: string) => Promise<void> }) {
  const [note, setNote] = useState("");
  const [working, setWorking] = useState(false);
  const flags = suggestionFlags(s);
  const needsNote = confirmNeedsNote(s);

  async function go(d: "confirmed" | "rejected") {
    setWorking(true);
    await onDecide(s, d, note);
    setWorking(false);
  }

  return (
    <li className="space-y-1.5 py-3">
      <p className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-medium">{identifierTypeLabel(s.identifier_type)}</span>
        <span className="num">{s.identifier}</span>
        <span className="text-xs text-text-muted">{s.match_status === "suggested" ? "suggested, not confirmed" : MATCH_STATUS_LABEL[s.match_status].toLowerCase()}</span>
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
        Match score <span className="num">{formatScore(s.score)}</span> · from {s.provider} · {s.issuer_name ?? (s.issuer_id != null ? `company #${s.issuer_id}` : "no company")}
        {s.security_id != null ? <> · security <span className="num">#{s.security_id}</span></> : null}
      </p>
      {s.decision_note ? <p className="max-w-[72ch] text-sm">Note: {s.decision_note}</p> : null}

      {s.match_status === "suggested" ? (
        <div className="space-y-2 pt-1">
          <label className="flex max-w-[72ch] flex-col gap-1 text-sm">
            <span className="font-medium">{needsNote ? `Why this is the right match (at least ${NOTE_MIN} characters, required to confirm)` : "Note (optional)"}</span>
            <input type="text" value={note} onChange={(e) => setNote(e.target.value)} disabled={working} className="h-9 border border-border bg-surface-raised px-2 text-sm" />
          </label>
          <p className="max-w-[72ch] text-xs text-text-muted">
            A decision is final and cannot be changed later. Confirming fills this company’s or security’s empty LEI or FIGI; it never overwrites one that is already set.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={working || !confirmAllowed(s, note)} onClick={() => void go("confirmed")}>
              Confirm
            </Button>
            <Button type="button" size="sm" variant="outline" disabled={working} onClick={() => void go("rejected")}>
              Reject
            </Button>
          </div>
        </div>
      ) : null}
    </li>
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
