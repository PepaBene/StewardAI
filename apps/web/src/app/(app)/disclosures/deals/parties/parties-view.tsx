"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { callApi } from "@/app/(app)/relationships/decision-form";
import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { httpFailure, networkFailure } from "@/lib/disclosure/keyed";

type Party = components["schemas"]["PartySuggestionOut"];

/**
 * Deal parties whose name has no issuer yet. Each lists the issuers the name could belong to; nothing
 * is matched until you confirm one, and a rejected match is not suggested again.
 */
export function PartiesPageView() {
  const [parties, setParties] = useState<Party[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  // two-step: the pending decision waits for a second press
  const [pending, setPending] = useState<{ id: number; issuer: number; action: "confirm" | "reject" } | null>(null);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/deals/parties/pending");
      if (r.data) {
        setParties(r.data);
        setLoadError(null);
      } else {
        setParties(null);
        setLoadError(httpFailure("The pending deal parties", r.response.status));
      }
    } catch (e) {
      setParties(null);
      setLoadError(networkFailure("The pending deal parties", e));
    }
  }, []);

  useEffect(() => {
    // The loader sets state only after the request returns, not during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function decide(p: Party, issuer: number, action: "confirm" | "reject") {
    if (inFlight.current) return;
    if (!pending || pending.id !== p.participant_id || pending.issuer !== issuer || pending.action !== action) {
      setPending({ id: p.participant_id, issuer, action });
      return;
    }
    inFlight.current = true;
    setBusy(p.participant_id);
    setPending(null);
    const api = getBrowserApiClient();
    const body = { issuer_id: issuer };
    const path = { participant_id: p.participant_id };
    const r = await callApi(
      () => (action === "confirm" ? api.POST("/disclosures/deals/parties/{participant_id}/confirm", { params: { path }, body }) : api.POST("/disclosures/deals/parties/{participant_id}/reject", { params: { path }, body })),
      action === "confirm" ? "Confirmed: this party is that issuer." : "Rejected: it will not be suggested again.",
    );
    setMessage(r);
    setBusy(null);
    inFlight.current = false;
    if (r.ok) await load();
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader kicker="Research & Verification" title="Deal parties to review" description="Names in deal filings that are not yet matched to an issuer. Confirm the right issuer or reject a wrong suggestion." aside={<Link href="/disclosures/deals" className="text-accent-ink hover:underline">Back to deals</Link>} />
      {message ? (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? "border border-border bg-surface-muted px-3 py-2 text-sm" : "border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm"}>
          {message.text}
        </p>
      ) : null}
      <Panel id="pending" title="Waiting for a decision" description="Each press asks once more before it records anything.">
        {loadError ? (
          <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
            {loadError} <button type="button" className="underline" onClick={() => void load()}>Try again</button>
          </p>
        ) : parties === null ? (
          <p className="text-sm text-text-muted">Loading…</p>
        ) : parties.length === 0 ? (
          <p className="text-sm text-text-muted">No deal party is waiting for review.</p>
        ) : (
          <ul className="divide-y divide-border-soft border-y border-border-soft">
            {parties.map((p) => (
              <li key={p.participant_id} className="space-y-2 py-3 text-sm">
                <p className="font-medium">{p.name} <span className="font-normal text-text-muted">· {p.role.replaceAll("_", " ")} · deal {p.deal_id}</span></p>
                {p.basis ? <p className="text-xs text-text-muted">Basis: {p.basis}</p> : null}
                {p.note ? <p className="text-xs text-text-muted">{p.note}</p> : null}
                {p.issuer_ids.length === 0 ? <p className="text-xs text-text-muted">No candidate issuer was found for this name.</p> : null}
                <ul className="space-y-1">
                  {p.issuer_ids.map((id) => {
                    const asking = pending?.id === p.participant_id && pending.issuer === id ? pending.action : null;
                    return (
                      <li key={id} className="flex flex-wrap items-center gap-2">
                        <span>Issuer <span className="num">#{id}</span></span>
                        <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void decide(p, id, "confirm")}>{asking === "confirm" ? "Yes, confirm this issuer" : "It is this issuer"}</Button>
                        <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void decide(p, id, "reject")}>{asking === "reject" ? "Yes, reject this issuer" : "It is not this issuer"}</Button>
                        {asking ? <Button type="button" size="sm" variant="ghost" onClick={() => setPending(null)}>Cancel</Button> : null}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
