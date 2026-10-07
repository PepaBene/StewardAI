"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { callApi } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import { ALERT_HREF, ALERT_KIND_LABEL, byKnown, splitByState, type DisclosureAlert } from "@/lib/disclosure/alerts";
import { formatDay } from "@/lib/disclosure/model";

/**
 * What the disclosure collectors found that you may want to read: a verified insider trade, a large
 * holder’s filing on a holding, a change in a customer or supplier claim. Each says what was filed
 * and when it became known, not why. Acknowledging one only moves it out of the open list.
 */
export function AlertsPageView() {
  const [alerts, setAlerts] = useState<DisclosureAlert[] | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/alerts", { params: { query: { include_acknowledged: true, limit: 200 } } });
      if (r.data) setAlerts(r.data);
      else setMessage({ ok: false, text: r.response.status === 401 || r.response.status === 403 ? "Sign in again (with MFA) and retry." : `Alerts failed to load (HTTP ${r.response.status}).` });
    } catch (e) {
      setMessage({ ok: false, text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
    }
  }, []);

  useEffect(() => {
    // The loader sets state only after the request returns, not during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function scan() {
    setBusy(true);
    try {
      const r = await getBrowserApiClient().POST("/disclosures/alerts/scan");
      if (r.data) {
        const d = r.data;
        const total = d.insider_trade + d.major_holder + d.relationship_change;
        setMessage({ ok: d.failed === 0, text: `${total} new ${total === 1 ? "alert" : "alerts"}${d.failed ? `, and ${d.failed} ${d.failed === 1 ? "check" : "checks"} could not run` : ""}.` });
        await load();
      } else setMessage({ ok: false, text: `The check failed (HTTP ${r.response.status}).` });
    } catch (e) {
      setMessage({ ok: false, text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(id: number) {
    const r = await callApi(() => getBrowserApiClient().POST("/disclosures/alerts/{alert_id}/acknowledge", { params: { path: { alert_id: id } } }), "Marked as read.");
    setMessage({ ok: r.ok, text: r.text });
    if (r.ok) await load();
  }

  const { open, done } = splitByState(alerts ?? []);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Disclosure alerts"
        description="Verified insider trades, large-holder filings and changes to customer and supplier claims for what you hold. Each states what was filed, not why."
        aside={
          <Button type="button" size="sm" onClick={() => void scan()} disabled={busy}>
            {busy ? "Checking…" : "Check for new filings"}
          </Button>
        }
      />

      {message ? (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? "border border-border bg-surface-muted px-3 py-2 text-sm" : "border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm"}>
          {message.text}
        </p>
      ) : null}

      <Panel id="open" title="To read" description="Newest first by the date each became known to us.">
        {alerts === null ? (
          <p className="text-sm text-text-muted">Loading alerts…</p>
        ) : open.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing is waiting. New filings for your holdings appear here after the next collection, or press “Check for new filings”.</p>
        ) : (
          <AlertList alerts={byKnown(open)} onAcknowledge={acknowledge} />
        )}
      </Panel>

      <Panel id="done" title="Already read" description="Kept so you can look back; nothing is deleted.">
        <label className="mb-2 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} className="size-4" />
          Show {done.length} read {done.length === 1 ? "alert" : "alerts"}
        </label>
        {showDone ? done.length === 0 ? <p className="text-sm text-text-muted">No alert has been marked as read.</p> : <AlertList alerts={byKnown(done)} /> : null}
      </Panel>
    </div>
  );
}

function AlertList({ alerts, onAcknowledge }: { alerts: readonly DisclosureAlert[]; onAcknowledge?: (id: number) => void }) {
  return (
    <ul className="divide-y divide-border-soft border-y border-border-soft">
      {alerts.map((a) => (
        <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
          <div className="min-w-0 max-w-[68ch] space-y-0.5">
            <p className="font-medium">
              <span className="num">{a.ticker}</span> · {a.headline}
            </p>
            <p className="text-xs text-text-muted">
              {ALERT_KIND_LABEL[a.kind]} · known <span className="num">{formatDay(a.known_at)}</span>
              {a.acknowledged_at ? <> · read <span className="num">{formatDay(a.acknowledged_at)}</span></> : null}
            </p>
            <p className="text-xs">
              <Link href={`${ALERT_HREF[a.kind]}`} className="text-accent-ink hover:underline">Open the full view</Link>
              {a.source_url ? (
                <>
                  {" "}
                  <a href={a.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Open the filing</a>
                </>
              ) : null}
            </p>
          </div>
          {onAcknowledge ? (
            <Button type="button" size="sm" variant="outline" onClick={() => onAcknowledge(a.id)}>
              Mark as read
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
