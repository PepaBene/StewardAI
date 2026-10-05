"use client";

import { useCallback, useMemo, useState } from "react";

import { useMutation } from "@/components/reference/action-bits";
import { Field, FormFrame, OwnerTag, SaveNote, SELECT_CLASS } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { emptyPeerDraft, parsePeerSet, peerRequest, validatePeer, validatePeerStatus, type PeerDraft, type PeerEntry } from "@/lib/reference/endpoint-forms";

const STATUS_LABEL = { suggested: "Suggested", confirmed: "Confirmed", rejected: "Rejected" } as const;

/**
 * GET/POST /holdings/{ticker}/peer-set and PUT .../peer-set/{id}/status. Suggested peers wait for
 * your decision and are left out of the comparison; confirmed peers feed it. Every change keeps
 * its reason in the peer's dated history.
 */
export function PeerSetEditor({ ticker, onChanged }: { ticker: string; onChanged?: () => void }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/peer-set", { params: { path: { ticker }, query: {} } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const peers = useMemo(() => parsePeerSet(raw), [raw]);
  const after = () => {
    reload();
    onChanged?.();
  };

  return (
    <section aria-label="Peer decisions" className="space-y-3 border-t border-border-soft pt-3">
      <div>
        <h3 className="text-base font-semibold">Peer decisions</h3>
        <p className="max-w-[68ch] text-sm text-text-muted">
          Only confirmed peers enter the comparison. Confirming needs supporting evidence; every decision keeps your reason.
        </p>
      </div>
      {state.kind === "loading" && !raw ? <p className="text-sm text-text-muted">Loading peers…</p> : null}
      {state.kind === "error" && !raw ? <p className="text-sm text-text-muted">Peers unavailable: {state.title}</p> : null}
      {raw && peers.length === 0 ? <p className="text-sm text-text-muted">No peers recorded for {ticker} yet. Add one below.</p> : null}
      {peers.length > 0 ? (
        <ul className="divide-y divide-border border border-border">
          {peers.map((p) => (
            <PeerRow key={p.id} ticker={ticker} peer={p} onChanged={after} />
          ))}
        </ul>
      ) : null}
      <AddPeer ticker={ticker} onSaved={after} />
    </section>
  );
}

function PeerRow({ ticker, peer, onChanged }: { ticker: string; peer: PeerEntry; onChanged: () => void }) {
  const [target, setTarget] = useState<"suggested" | "confirmed" | "rejected" | null>(null);
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const errors = target ? validatePeerStatus(target, reason, evidence, peer.evidenceId !== null) : {};
  const options = (["confirmed", "rejected", "suggested"] as const).filter((s) => s !== peer.status);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!target || Object.keys(errors).length > 0) return;
    const ev = evidence.trim() === "" ? null : Number(evidence);
    const ok = await run(
      () => getBrowserApiClient().PUT("/holdings/{ticker}/peer-set/{peer_id}/status", { params: { path: { ticker, peer_id: peer.id } }, body: { status: target, reason: reason.trim(), evidence_id: ev } }),
      () => `${peer.ticker} is now ${STATUS_LABEL[target].toLowerCase()}.`,
      onChanged,
    );
    if (ok) {
      setTarget(null);
      setReason("");
      setEvidence("");
      setTouched(false);
    }
  }

  return (
    <li className="space-y-2 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-sm font-medium">{peer.ticker}</span>
        <span
          className={
            peer.status === "confirmed"
              ? "border border-accent px-1.5 py-0.5 text-[11px] font-medium text-accent-ink"
              : peer.status === "rejected"
                ? "border border-border px-1.5 py-0.5 text-[11px] font-medium text-text-muted line-through"
                : "border border-dashed border-border px-1.5 py-0.5 text-[11px] font-medium text-text-muted"
          }
        >
          {STATUS_LABEL[peer.status]}
        </span>
        <span className="text-xs text-text-muted">
          {peer.basis ?? "Basis unknown"}, from {peer.source ?? "an unknown source"}
          {peer.evidenceId !== null ? `, evidence #${peer.evidenceId}` : ", no evidence on file"}
          {peer.validFrom ? `, from ${formatDate(peer.validFrom)}` : ""}
          {peer.validTo ? ` to ${formatDate(peer.validTo)}` : ""}
        </span>
        <span className="ml-auto flex gap-2">
          {options.map((s) => (
            <Button key={s} type="button" size="sm" variant={target === s ? "default" : "outline"} onClick={() => setTarget(target === s ? null : s)}>
              {s === "confirmed" ? "Confirm" : s === "rejected" ? "Reject" : "Back to suggested"}
            </Button>
          ))}
        </span>
      </div>
      {peer.history.length > 0 ? (
        <details className="text-xs text-text-muted">
          <summary className="cursor-pointer">Decision history ({peer.history.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {peer.history.map((h, i) => (
              <li key={i}>
                {h.at ? `${formatDate(h.at)}: ` : ""}
                {h.status}
                {h.reason ? `, ${h.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {target ? (
        <form onSubmit={submit} noValidate aria-label={`${STATUS_LABEL[target]} ${peer.ticker}`} className="space-y-2 bg-surface-muted p-3">
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
            <Field label="Reason" error={touched ? errors.reason : undefined} hint={<OwnerTag />}>
              <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
            </Field>
            <Field label="Evidence id" error={touched ? errors.evidenceId : undefined} hint={peer.evidenceId !== null ? "Optional: already has evidence" : "Needed to confirm"}>
              <Input inputMode="numeric" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" disabled={state.kind === "saving"}>
              Save as {STATUS_LABEL[target].toLowerCase()}
            </Button>
            <SaveNote state={state} />
          </div>
        </form>
      ) : (
        <SaveNote state={state} />
      )}
    </li>
  );
}

function AddPeer({ ticker, onSaved }: { ticker: string; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PeerDraft>(emptyPeerDraft);
  const [touched, setTouched] = useState(false);
  const { state, run } = useMutation();
  const errors = validatePeer(draft, ticker);
  const set = <K extends keyof PeerDraft>(k: K, v: PeerDraft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const show = (k: keyof PeerDraft) => (touched ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length > 0) return;
    const ok = await run(
      () => getBrowserApiClient().POST("/holdings/{ticker}/peer-set", { params: { path: { ticker } }, body: peerRequest(draft) }),
      () => `Added ${draft.peer.trim()} as ${draft.status}.`,
      onSaved,
    );
    if (ok) {
      setDraft(emptyPeerDraft());
      setTouched(false);
    }
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
          Add a peer
        </Button>
        <SaveNote state={state} />
      </div>
    );
  }
  return (
    <form onSubmit={submit} noValidate aria-label={`Add a peer for ${ticker}`}>
      <FormFrame title={`Add a peer for ${ticker}`}>
        <p className="text-xs text-text-muted">Adding a peer that already exists changes nothing; use its buttons above to change its status.</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Peer ticker" error={show("peer")}>
            <Input value={draft.peer} onChange={(e) => set("peer", e.target.value)} maxLength={32} />
          </Field>
          <Field label="Why it is a peer" error={show("basis")} hint="For example: same end market">
            <Input value={draft.basis} onChange={(e) => set("basis", e.target.value)} maxLength={80} />
          </Field>
          <Field label="Source" error={show("source")}>
            <Input value={draft.source} onChange={(e) => set("source", e.target.value)} maxLength={80} />
          </Field>
          <Field label="Status" hint="Suggested waits for your decision">
            <select className={SELECT_CLASS} value={draft.status} onChange={(e) => set("status", e.target.value as PeerDraft["status"])}>
              <option value="suggested">Suggested</option>
              <option value="confirmed">Confirmed</option>
            </select>
          </Field>
          <Field label="Evidence id" error={show("evidenceId")} hint={draft.status === "confirmed" ? "Required to confirm" : "Optional"}>
            <Input inputMode="numeric" value={draft.evidenceId} onChange={(e) => set("evidenceId", e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Valid from" error={show("validFrom")} hint="YYYY-MM-DD">
              <Input value={draft.validFrom} onChange={(e) => set("validFrom", e.target.value)} placeholder="Optional" />
            </Field>
            <Field label="Valid to" error={show("validTo")}>
              <Input value={draft.validTo} onChange={(e) => set("validTo", e.target.value)} placeholder="Optional" />
            </Field>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={state.kind === "saving"}>
            Add peer
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
            Close
          </Button>
          <SaveNote state={state} />
        </div>
      </FormFrame>
    </form>
  );
}
