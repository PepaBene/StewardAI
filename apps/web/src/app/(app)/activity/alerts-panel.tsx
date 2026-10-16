"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { EntityName } from "@/components/entity-name";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { SeverityBadge } from "@/components/news/severity-badge";
import { SeverityDot } from "@/components/news/severity-timeline";
import { Button } from "@/components/ui/button";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { CompactRow } from "@/components/ui/compact-row";
import { ResponsiveTable } from "@/components/ui/responsive-table";
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
    <section id="alerts" aria-labelledby="alerts-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="alerts-heading" className="text-[22px] leading-tight font-semibold">
            Alerts
          </h2>
          <p className="text-sm text-text-muted">
            The monitor checks every 15 minutes for failed jobs, missed scheduled runs and holdings with no news coverage;
            each condition alerts once. Their severity and routing are in{" "}
            <Link href="/config/notifications" className="text-accent-ink hover:underline">
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
        <ResponsiveTable
          rows={load.alerts}
          rowKey={(a) => String(a.id)}
          pageSize={10}
          caption={all ? "All alerts, newest first" : "Monitoring alerts, newest first"}
          columns={[
            {
              key: "alert",
              header: "Alert",
              className: "max-w-xl",
              cell: (a) => (
                <div className="space-y-1">
                  <p className="flex flex-wrap items-center gap-2">
                    <SeverityDot severity={a.severity} />
                    <SeverityBadge severity={a.severity} />
                    <span className="font-medium">{alertEventLabel(a)}</span>
                    {a.kind !== "system" ? <span className="text-xs text-text-muted">{ALERT_KIND_LABELS[a.kind]}</span> : null}
                  </p>
                  <p className="break-words">{alertHeadline(a.message)}</p>
                  {a.cluster_id !== null ? (
                    <Link href={`/news?story=${a.cluster_id}`} className="text-xs text-accent-ink hover:underline">
                      Open the story
                    </Link>
                  ) : null}
                </div>
              ),
            },
            {
              key: "holding",
              header: "Holding",
              cell: (a) =>
                a.ticker ? (
                  <Link href={`/companies/${encodeURIComponent(a.ticker)}`} className="text-accent-ink hover:underline">
                    <EntityName id={a.ticker} chip={false} />
                  </Link>
                ) : (
                  <span className="text-text-muted">None</span>
                ),
            },
            {
              key: "when",
              header: "Sent",
              cell: (a) => (
                <>
                  <time dateTime={a.created_at} title={formatDateTime(a.created_at)}>
                    {formatRelative(new Date(a.created_at), now)}
                  </time>
                  <span className="block text-xs text-text-muted">
                    {CHANNEL_LABELS[a.channel] ?? a.channel} · {alertOutcome(a)}
                  </span>
                </>
              ),
            },
            {
              key: "feedback",
              header: "Was it right?",
              cell: (a) => <FeedbackControl target={{ kind: "alert", id: a.id }} label={`alert “${alertHeadline(a.message).slice(0, 60)}”`} />,
            },
          ]}
          compact={(a) => (
            <div>
              <CompactRow
                name={alertEventLabel(a)}
                symbol={a.severity}
                primary={formatRelative(new Date(a.created_at), now)}
                secondary={[alertHeadline(a.message).slice(0, 60)]}
              />
              <div className="px-4 pb-2">
                <FeedbackControl target={{ kind: "alert", id: a.id }} label={`alert “${alertHeadline(a.message).slice(0, 60)}”`} />
              </div>
            </div>
          )}
        />
      ) : null}
    </section>
  );
}
