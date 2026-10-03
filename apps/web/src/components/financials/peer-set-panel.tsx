"use client";

import { useState } from "react";

import { StatePlaceholder } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { PeerEntry, PeerStatus } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<PeerStatus, string> = { suggested: "Suggested", confirmed: "Confirmed", rejected: "Rejected" };
const ORDER: PeerStatus[] = ["confirmed", "suggested", "rejected"];

/** Pure list of peers by status: only confirmed peers enter any average. */
export function PeerSetList({ peers }: { peers: readonly PeerEntry[] }) {
  if (peers.length === 0) {
    return <StatePlaceholder state="not_covered" reason="No peer has been recorded for this holding. Candidates arrive from the Finnhub research probe or are added by the owner; none is averaged until confirmed." minHeight={80} />;
  }
  const counts = ORDER.map((s) => `${peers.filter((p) => p.status === s).length} ${s}`).join(" · ");
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <p className="num text-xs text-text-muted">{counts}</p>
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[30rem] text-sm">
          <caption className="sr-only">Peer set with status, accounting basis, source and known-since date</caption>
          <thead className="bg-surface-muted text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <tr>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Peer</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Status</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Basis</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Source</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Known since</th>
            </tr>
          </thead>
          <tbody>
            {[...peers].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || a.peerTicker.localeCompare(b.peerTicker)).map((p) => (
              <tr key={p.id} className="border-b border-border-soft">
                <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-medium">{p.peerTicker}</th>
                <td className="px-3 py-1.5">{STATUS_LABEL[p.status]}</td>
                <td className="px-3 py-1.5 text-text-muted">{p.basis ?? "not stated"}</td>
                <td className="px-3 py-1.5 text-text-muted">{p.source ?? "not stated"}{p.evidenceId !== null ? ` · evidence #${p.evidenceId}` : ""}</td>
                <td className="num px-3 py-1.5 text-right text-text-muted">{p.knownAt ? formatDate(p.knownAt.slice(0, 10)) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Owner validation of peers: confirm, reject or return to suggested. The change is dated in
 * the peer's history; confirming needs evidence (an existing evidence id) and a reason of at
 * least three characters, as the API requires. Errors from the API are shown verbatim.
 */
export function PeerSetPanel({ ticker, peers, onChanged }: { ticker: string; peers: readonly PeerEntry[]; onChanged: () => void }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const peer = peers.find((p) => p.id === selected) ?? null;

  async function change(status: PeerStatus) {
    if (!peer) return;
    setBusy(true);
    setMessage(null);
    try {
      const evidenceId = evidence.trim() === "" ? undefined : Number(evidence);
      if (evidenceId !== undefined && !Number.isInteger(evidenceId)) {
        setMessage({ tone: "error", text: "Evidence id must be a whole number." });
        return;
      }
      const r = await getBrowserApiClient().PUT("/holdings/{ticker}/peer-set/{peer_id}/status", {
        params: { path: { ticker, peer_id: peer.id } },
        body: { status, reason: reason.trim() || undefined, evidence_id: evidenceId },
      });
      if (r.response.ok) {
        setMessage({ tone: "ok", text: `${peer.peerTicker} is now ${status}.` });
        setReason("");
        setEvidence("");
        onChanged();
      } else {
        const detail = r.error && typeof r.error === "object" && "detail" in r.error ? JSON.stringify((r.error as { detail: unknown }).detail) : "";
        setMessage({ tone: "error", text: `Not changed (HTTP ${r.response.status}). ${detail}` });
      }
    } catch (err) {
      setMessage({ tone: "error", text: `Backend unreachable: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Blueprint as="section" aria-labelledby="peer-set-heading" className="flex min-w-0 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Peer set · owner decision" title={<span id="peer-set-heading">Peers and their status</span>} />
      <PeerSetList peers={peers} />
      {peers.length > 0 ? (
        <fieldset className="flex min-w-0 flex-col gap-2 border border-border-soft p-3">
          <legend className="px-1 text-xs font-medium text-text-muted">Validate a peer</legend>
          <div className="flex flex-wrap items-end gap-2 text-xs">
            <label className="flex flex-col gap-0.5">
              <span className="text-text-muted">Peer</span>
              <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value === "" ? null : Number(e.target.value))} className="border border-border bg-surface-raised px-2 py-1 text-text">
                <option value="">Choose…</option>
                {peers.map((p) => (
                  <option key={p.id} value={p.id}>{`${p.peerTicker} (${p.status})`}</option>
                ))}
              </select>
            </label>
            <label className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="text-text-muted">Reason (min. 3 characters)</span>
              <input value={reason} onChange={(e) => setReason(e.target.value)} className="min-w-0 border border-border bg-surface-raised px-2 py-1 text-text" />
            </label>
            <label className="flex w-28 flex-col gap-0.5">
              <span className="text-text-muted">Evidence id</span>
              <input value={evidence} inputMode="numeric" onChange={(e) => setEvidence(e.target.value)} className="num border border-border bg-surface-raised px-2 py-1 text-text" />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            {(["confirmed", "rejected", "suggested"] as const).map((s) => (
              <button
                key={s}
                type="button"
                disabled={busy || !peer || peer.status === s || (reason.trim().length > 0 && reason.trim().length < 3)}
                onClick={() => void change(s)}
                className={cn("border border-border px-3 py-1 text-xs font-semibold outline-hidden hover:bg-border-soft focus-visible:outline-2 focus-visible:outline-focus disabled:opacity-40")}
              >
                {s === "confirmed" ? "Confirm" : s === "rejected" ? "Reject" : "Back to suggested"}
              </button>
            ))}
          </div>
          {message ? (
            <p role={message.tone === "error" ? "alert" : "status"} className="text-xs">
              {message.text}
            </p>
          ) : null}
        </fieldset>
      ) : null}
    </Blueprint>
  );
}
