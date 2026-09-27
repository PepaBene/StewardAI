"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { SeverityBadge } from "@/components/news/severity-badge";
import { SeverityDot } from "@/components/news/severity-timeline";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type AlertOut } from "@/lib/api/client";
import { ALERT_KIND_LABELS, alertEventLabel, alertHeadline, alertOutcome } from "@/lib/activity/activity";
import { CHANNEL_LABELS } from "@/lib/config/news-forms";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; alerts: AlertOut[] };

/**
 * Alert decisions, newest first. By default only monitoring (system) alerts: failed jobs,
 * missed runs and coverage gaps; "All alerts" adds news, thesis breaches, portfolio and test
 * alerts. Every alert takes wrong / irrelevant / useful.
 */
export function AlertsPanel() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const all = params.get("alerts") === "all";
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const now = useNow();

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/alerts", { params: { query: { limit: 100, kind: all ? undefined : ["system"] } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", alerts: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => !cancelled && setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) }));
    return () => {
      cancelled = true;
    };
  }, [all, reloadKey]);

  function show(nextAll: boolean) {
    const next = new URLSearchParams(params.toString());
    if (nextAll) next.set("alerts", "all");
    else next.delete("alerts");
    const text = next.toString();
    router.replace(`${pathname}${text ? `?${text}` : ""}#alerts`, { scroll: false });
  }

  const toggle = (active: boolean) =>
    cn(
      "rounded-md border px-2.5 py-1 text-sm",
      active ? "border-accent bg-surface-muted font-medium text-text" : "border-border text-text-muted hover:text-text",
    );

  return (
    <section id="alerts" aria-labelledby="alerts-heading" className="scroll-mt-24 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="alerts-heading" className="text-[22px] leading-tight font-semibold">
            Alerts
          </h2>
          <p className="text-sm text-text-muted">
            The monitor checks every 15 minutes for failed jobs, missed scheduled runs and holdings with no news coverage;
            each condition alerts once. Their severity and routing are in{" "}
            <Link href="/config/notifications" className="text-accent hover:underline">
              Config: Notifications
            </Link>
            .
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div role="group" aria-label="Which alerts" className="flex gap-1">
            <button type="button" aria-pressed={!all} className={toggle(!all)} onClick={() => show(false)}>
              Monitoring only
            </button>
            <button type="button" aria-pressed={all} className={toggle(all)} onClick={() => show(true)}>
              All alerts
            </button>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading alerts…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the alerts">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.alerts.length === 0 ? (
        <EmptyState title={all ? "No alerts yet" : "No monitoring alerts"}>
          {all ? "Alerts appear when a confirmed event matches a notification rule." : "No job has failed, no run was missed and every holding has news coverage."}
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.alerts.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised" aria-label={all ? "All alerts" : "Monitoring alerts"}>
          {load.alerts.map((a) => (
            <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 p-3 text-sm">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="flex flex-wrap items-center gap-2">
                  <SeverityDot severity={a.severity} />
                  <SeverityBadge severity={a.severity} />
                  <span className="font-medium">{alertEventLabel(a)}</span>
                  {a.kind !== "system" ? <span className="text-xs text-text-muted">{ALERT_KIND_LABELS[a.kind]}</span> : null}
                  {a.ticker ? (
                    <Link href={`/holdings/${encodeURIComponent(a.ticker)}`} className="font-mono text-xs text-accent hover:underline">
                      {a.ticker}
                    </Link>
                  ) : null}
                </p>
                <p className="break-words">{alertHeadline(a.message)}</p>
                <p className="num flex flex-wrap gap-x-2 text-xs text-text-muted">
                  <time dateTime={a.created_at} title={formatDateTime(a.created_at)}>
                    {formatRelative(new Date(a.created_at), now)}
                  </time>
                  <span>· {CHANNEL_LABELS[a.channel] ?? a.channel}</span>
                  <span>· {alertOutcome(a)}</span>
                  {a.cluster_id !== null ? (
                    <Link href={`/news?story=${a.cluster_id}`} className="text-accent hover:underline">
                      · the story
                    </Link>
                  ) : null}
                </p>
              </div>
              <FeedbackControl target={{ kind: "alert", id: a.id }} label={`alert “${alertHeadline(a.message).slice(0, 60)}”`} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
