"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Panel } from "@/components/oversight/panel";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { callApi } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import { ALERT_HREF, ALERT_KIND_LABEL, byKnown, digestKindLabel, digestOrder, forTicker, scanTotal, splitByState, whyLines, type DigestGroup, type DisclosureAlert } from "@/lib/disclosure/alerts";
import { formatDay } from "@/lib/disclosure/model";

const UNREAD_LIMIT = 200;
const RECENT_LIMIT = 50;

/**
 * What the disclosure collectors found that you may want to read: a verified insider trade, a large
 * holder’s filing on a holding, a change in a customer or supplier claim. Each says what was filed
 * and when it became known, and which rule fired (never a motive). Acknowledging one only moves it out of the open list.
 */
export function AlertsPageView() {
  const [alerts, setAlerts] = useState<DisclosureAlert[] | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [digest, setDigest] = useState<DigestGroup[] | null>(null);
  const [digestError, setDigestError] = useState<string | null>(null);
  const [ticker, setTicker] = useState<string | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [unreadCapped, setUnreadCapped] = useState(false);
  const acking = useRef(new Set<number>());

  // Unread alerts are fetched on their own, so a long history of read ones can never push an unread one out of the list.
  const load = useCallback(async () => {
    try {
      const api = getBrowserApiClient();
      const [unread, recent] = await Promise.all([
        api.GET("/disclosures/alerts", { params: { query: { include_acknowledged: false, limit: UNREAD_LIMIT } } }),
        api.GET("/disclosures/alerts", { params: { query: { include_acknowledged: true, limit: RECENT_LIMIT } } }),
      ]);
      if (unread.data && recent.data) {
        const unreadIds = new Set(unread.data.map((x) => x.id));
        setAlerts([...unread.data, ...recent.data.filter((x) => x.acknowledged_at != null && !unreadIds.has(x.id))]);
        setUnreadCapped(unread.data.length >= UNREAD_LIMIT);
        setLoadError(null);
      } else {
        const status = (unread.data ? recent : unread).response.status;
        setAlerts(null); // never keep an older list on screen after a failed reload
        setLoadError(status === 401 || status === 403 ? "Sign in again (with MFA) and retry." : `Alerts failed to load (HTTP ${status}).`);
      }
    } catch (e) {
      setAlerts(null);
      setLoadError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  const loadDigest = useCallback(async () => {
    try {
      const r = await getBrowserApiClient().GET("/disclosures/alerts/digest");
      if (r.data) {
        setDigest(r.data);
        setDigestError(null);
      } else setDigestError(`The digest failed to load (HTTP ${r.response.status}). The list below is unaffected.`);
    } catch (e) {
      setDigestError(`Backend unreachable: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, []);

  useEffect(() => {
    // The loaders set state only after the request returns, not during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadDigest();
  }, [loadDigest]);

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
        const total = scanTotal(d);
        const tasks = d.review_tasks ? `, ${d.review_tasks} ${d.review_tasks === 1 ? "item" : "items"} sent to review instead of alerting` : "";
        setMessage({ ok: d.failed === 0, text: `${total} new ${total === 1 ? "alert" : "alerts"}${tasks}${d.failed ? `, and ${d.failed} ${d.failed === 1 ? "check" : "checks"} could not run` : ""}.` });
        await Promise.all([load(), loadDigest()]);
      } else setMessage({ ok: false, text: `The check failed (HTTP ${r.response.status}).` });
    } catch (e) {
      setMessage({ ok: false, text: `Backend unreachable: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(id: number) {
    if (acking.current.has(id)) return; // a double click must not post twice
    acking.current.add(id);
    const r = await callApi(() => getBrowserApiClient().POST("/disclosures/alerts/{alert_id}/acknowledge", { params: { path: { alert_id: id } } }), "Marked as read.");
    setMessage({ ok: r.ok, text: r.text });
    acking.current.delete(id);
    if (r.ok) await Promise.all([load(), loadDigest()]);
  }

  const { open, done } = splitByState(alerts ?? []);
  const shownOpen = forTicker(open, ticker);

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

      <Panel id="digest" title="Read first" description="Unread alerts grouped by ticker, most alerts first. Choose a ticker to show only its alerts below.">
        {digestError ? (
          <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{digestError}</p>
        ) : digest === null ? (
          <p className="text-sm text-text-muted">Loading the digest…</p>
        ) : digest.length === 0 ? (
          <p className="text-sm text-text-muted">No unread alerts to group. Press “Check for new filings” to look for new ones.</p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {digestOrder(digest).map((g) => {
              const active = g.ticker === ticker;
              return (
                <li key={g.ticker}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={() => {
                      setTicker(active ? null : g.ticker);
                      document.getElementById("open")?.scrollIntoView({ block: "start" });
                    }}
                    className={`block w-full border p-3 text-left text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none ${active ? "border-accent bg-accent text-accent-contrast" : "border-border bg-surface-raised hover:bg-surface-muted"}`}
                  >
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="num font-semibold">{g.ticker}</span>
                      <span className="num">{g.count} unread</span>
                    </span>
                    <span className="mt-0.5 block text-xs">{g.kinds.map(digestKindLabel).join(", ")} · latest known <span className="num">{formatDay(g.latest_known_at)}</span></span>
                    <span className="mt-1 block">{g.headlines[0]}</span>
                    {g.headlines.length > 1 ? <span className="block text-xs">and {g.headlines.length - 1} more</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <Panel id="open" title={ticker ? `To read: ${ticker}` : "To read"} description="Newest first by the date each became known to us.">
        {ticker ? (
          <p className="mb-2 text-sm">
            Showing <span className="num">{ticker}</span> only.{" "}
            <button type="button" onClick={() => setTicker(null)} className="text-accent-ink hover:underline">Show all tickers</button>
          </p>
        ) : null}
        {alerts === null ? (
          loadError ? <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">{loadError} <button type="button" className="underline" onClick={() => void load()}>Try again</button></p> : <p className="text-sm text-text-muted">Loading alerts…</p>
        ) : open.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing is waiting. New filings for your holdings appear here after the next collection, or press “Check for new filings”.</p>
        ) : shownOpen.length === 0 ? (
          <p className="text-sm text-text-muted">No unread alerts for {ticker}. Choose “Show all tickers” to see the rest.</p>
        ) : (
          <>
            {unreadCapped ? <p role="note" className="mb-2 text-sm text-text-muted">Showing the newest {UNREAD_LIMIT} unread alerts; mark some as read to see the rest.</p> : null}
            <AlertList alerts={byKnown(shownOpen)} onAcknowledge={acknowledge} />
          </>
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
            <details className="text-xs">
              <summary className="cursor-pointer text-accent-ink hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none">Why this fired</summary>
              <ul className="mt-1 list-disc pl-4 text-text-muted">
                {whyLines(a.why_fired).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </details>
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
