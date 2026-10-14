"use client";

import { LoaderCircleIcon, RefreshCwIcon, Undo2Icon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";

import { type OverrideReason, ReasonChips } from "./reason-chips";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  AUTOPILOT_TABS,
  canUndo,
  confidenceLabel,
  deciderLabel,
  modeHelp,
  modeLabel,
  parseTab,
  precisionLabel,
  statusLabel,
  TAB_LABELS,
  waitingLabel,
  type AutoDecision,
  type KindCalibration,
  type KindSummary,
  type NeedsYouItem,
} from "@/lib/autopilot";
import { cn } from "@/lib/utils";

type Load<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: T };

function useLoad<T>(
  fetcher: () => Promise<{ data?: T; response: Response }>,
  reloadKey: string | number,
): Load<T> {
  const [load, setLoad] = useState<Load<T>>({ kind: "loading" });
  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then(({ data, response }) => {
        if (cancelled) return;
        setLoad(
          data
            ? { kind: "ok", data }
            : {
                kind: "error",
                message: `The API answered HTTP ${response.status}.`,
              },
        );
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setLoad({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
    };
    // The fetcher is rebuilt each render; the key says when to fetch again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);
  return load;
}

/** What the app settled by itself, what it could not, and how far it has earned the right to act alone. */
export function AutopilotView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  const api = getBrowserApiClient();

  const needs = useLoad(() => api.GET("/autopilot/needs-you"), reloadKey);
  const summary = useLoad(() => api.GET("/autopilot/summary"), reloadKey);
  const calibration = useLoad(
    () => api.GET("/autopilot/calibration"),
    reloadKey,
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="Autopilot"
        description="The app settles its own review queues and logs every decision. Only what it could not settle with confidence waits here for you."
        aside={
          <Button variant="ghost" size="sm" onClick={reload}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />

      <Blueprint aria-labelledby="autopilot-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
          <h2
            id="autopilot-heading"
            className="text-[22px] leading-tight font-semibold"
          >
            {TAB_LABELS[tab]}
          </h2>
          <nav
            aria-label="Show"
            className="flex flex-wrap border border-border"
          >
            {AUTOPILOT_TABS.map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={tab === t}
                onClick={() =>
                  router.replace(
                    t === "needs-you" ? pathname : `${pathname}?tab=${t}`,
                    { scroll: false },
                  )
                }
                className={cn(
                  "border-l border-border px-3 py-1.5 text-xs first:border-l-0",
                  tab === t
                    ? "bg-accent text-accent-foreground"
                    : "text-text hover:bg-border-soft",
                )}
              >
                {TAB_LABELS[t]}
                {t === "needs-you" && needs.kind === "ok" ? (
                  <span className="num"> · {needs.data.count}</span>
                ) : null}
              </button>
            ))}
          </nav>
        </div>

        {tab === "needs-you" ? (
          <NeedsYouPanel load={needs} onChanged={reload} />
        ) : null}
        {tab === "decisions" ? (
          <DecisionsPanel
            kinds={summary.kind === "ok" ? summary.data.kinds : []}
            reloadKey={reloadKey}
            onChanged={reload}
          />
        ) : null}
        {tab === "kinds" ? <KindsPanel load={summary} /> : null}
        {tab === "calibration" ? (
          <CalibrationPanel load={calibration} />
        ) : null}
      </Blueprint>
    </div>
  );
}

function Loading({ what }: { what: string }) {
  return (
    <p
      className="flex items-center gap-2 px-5 py-4 text-sm text-text-muted"
      role="status"
    >
      <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
      Loading {what}…
    </p>
  );
}

function NeedsYouPanel({
  load,
  onChanged,
}: {
  load: Load<{ count: number; items: NeedsYouItem[] }>;
  onChanged: () => void;
}) {
  if (load.kind === "loading") return <Loading what="the list" />;
  if (load.kind === "error")
    return (
      <div className="p-5">
        <AlertMessage title="Could not load the list">
          {load.message}
        </AlertMessage>
      </div>
    );
  if (load.data.items.length === 0)
    return (
      <div className="p-5">
        <EmptyState title="Nothing needs you">
          The autopilot settled everything it looked at, or is still watching.
          Anything it cannot settle with confidence appears here with its
          recommendation.
        </EmptyState>
      </div>
    );
  return (
    <ul className="m-0 list-none p-0">
      {load.data.items.map((item) => (
        <NeedsYouRow
          key={`${item.kind_of_item}-${item.id}`}
          item={item}
          onChanged={onChanged}
        />
      ))}
    </ul>
  );
}

function NeedsYouRow({
  item,
  onChanged,
}: {
  item: NeedsYouItem;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const api = getBrowserApiClient();
  const path = { decision_id: item.id };
  const spot = item.kind_of_item === "spot_check";
  const notice = item.kind_of_item === "notice";

  async function run(
    name: string,
    call: () => Promise<{ response: Response; error?: unknown }>,
  ) {
    setPending(name);
    setError(null);
    try {
      const { response, error: body } = await call();
      if (!response.ok) {
        const detail = (body as { detail?: unknown } | undefined)?.detail;
        setError(
          typeof detail === "string" ? detail : `HTTP ${response.status}`,
        );
      } else onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  if (notice) {
    return (
      <li className="border-b border-border-soft px-5 py-4 last:border-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p className="text-[15px] font-medium">{item.label}</p>
          <span className="text-xs text-text-muted">
            Open {waitingLabel(item.age_days)}
          </span>
        </div>
        <p className="mt-1.5 max-w-prose text-sm">{item.reasons}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Link
            href={item.href}
            className="text-sm text-accent-ink hover:underline"
          >
            {item.href.startsWith("http") ? "Get the key" : "Open it"}
          </Link>
          <Button
            size="sm"
            variant="outline"
            disabled={pending !== null}
            onClick={() =>
              run("dismiss", () =>
                api.POST("/autopilot/notices/{task_id}/dismiss", {
                  params: { path: { task_id: item.id } },
                }),
              )
            }
          >
            {pending === "dismiss" ? "Dismissing…" : "Dismiss"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending !== null}
            onClick={() =>
              run("snooze", () =>
                api.POST("/autopilot/notices/{task_id}/snooze", {
                  params: { path: { task_id: item.id } },
                  body: { days: 7 },
                }),
              )
            }
          >
            {pending === "snooze" ? "Snoozing…" : "Snooze a week"}
          </Button>
        </div>
        {error ? (
          <p role="alert" className="mt-2 text-sm text-loss">
            {error}
          </p>
        ) : null}
      </li>
    );
  }

  return (
    <li className="border-b border-border-soft px-5 py-4 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-[15px] font-medium">
          <span className="mr-2 font-mono text-xs font-semibold">
            {item.ticker ?? "—"}
          </span>
          {item.label}
        </p>
        <span className="text-xs text-text-muted">
          {spot
            ? "Applied by the autopilot, worth a look"
            : `Waiting ${waitingLabel(item.age_days)}`}
        </span>
      </div>
      <p className="mt-1.5 max-w-prose text-sm">
        {spot ? "Decided" : "Suggests"}{" "}
        <strong className="font-semibold">{item.recommendation}</strong> (
        {confidenceLabel(item.confidence)} sure). {item.reasons}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {spot ? (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={pending !== null}
              onClick={() =>
                run("ok", () =>
                  api.POST("/autopilot/decisions/{decision_id}/review", {
                    params: { path },
                    body: { verdict: "ok" },
                  }),
                )
              }
            >
              Looks right
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={pending !== null}
              onClick={() => setAsking(true)}
            >
              <Undo2Icon aria-hidden="true" />
              Wrong, undo it
            </Button>
          </>
        ) : item.can_accept ? (
          <Button
            size="sm"
            disabled={pending !== null}
            onClick={() =>
              run("accept", () =>
                api.POST("/autopilot/decisions/{decision_id}/accept", {
                  params: { path },
                }),
              )
            }
          >
            {pending === "accept" ? "Applying…" : `Apply: ${item.recommendation}`}
          </Button>
        ) : null}
        <Link
          href={item.href}
          className="text-sm text-accent-ink hover:underline"
        >
          Decide it myself
        </Link>
      </div>
      {asking ? (
        <ReasonChips
          disabled={pending !== null}
          onCancel={() => setAsking(false)}
          onPick={(reason) => {
            setAsking(false);
            void run("undo", () =>
              api.POST("/autopilot/decisions/{decision_id}/revert", {
                params: { path },
                body: reason ? { reason_code: reason } : undefined,
              }),
            );
          }}
        />
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-sm text-loss">
          {error}
        </p>
      ) : null}
    </li>
  );
}

function DecisionsPanel({
  kinds,
  reloadKey,
  onChanged,
}: {
  kinds: KindSummary[];
  reloadKey: number;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const api = getBrowserApiClient();
  const load = useLoad(
    () =>
      api.GET("/autopilot/decisions", {
        params: {
          query: {
            kind: kind || undefined,
            status: (status || undefined) as AutoDecision["status"] | undefined,
            limit: 100,
          },
        },
      }),
    `${reloadKey}:${kind}:${status}`,
  );
  return (
    <div>
      <div className="flex flex-wrap gap-3 border-b border-border-soft px-5 py-3 text-sm">
        <label className="flex items-center gap-2">
          Queue
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="border border-border bg-surface px-2 py-1"
          >
            <option value="">All</option>
            {kinds.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Outcome
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="border border-border bg-surface px-2 py-1"
          >
            <option value="">All</option>
            {(
              ["applied", "shadow", "escalated", "reverted", "closed"] as const
            ).map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {load.kind === "loading" ? <Loading what="decisions" /> : null}
      {load.kind === "error" ? (
        <div className="p-5">
          <AlertMessage title="Could not load decisions">
            {load.message}
          </AlertMessage>
        </div>
      ) : null}
      {load.kind === "ok" && load.data.length === 0 ? (
        <div className="p-5">
          <EmptyState title="No decisions yet">
            The autopilot runs every six hours. Each decision it makes, or only
            logs while it is being checked, is listed here with its evidence.
          </EmptyState>
        </div>
      ) : null}
      {load.kind === "ok" && load.data.length > 0 ? (
        <ul className="m-0 list-none p-0">
          {load.data.map((d) => (
            <DecisionRow
              key={d.id}
              decision={d}
              label={kinds.find((k) => k.kind === d.kind)?.label ?? d.kind}
              onChanged={onChanged}
            />
          ))}
        </ul>
      ) : null}
    </div>
  );
}


function DecisionRow({
  decision: d,
  label,
  onChanged,
}: {
  decision: AutoDecision;
  label: string;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  async function undo(reason: OverrideReason | null) {
    setAsking(false);
    setPending(true);
    setError(null);
    try {
      const { response, error: body } = await getBrowserApiClient().POST(
        "/autopilot/decisions/{decision_id}/revert",
        {
          params: { path: { decision_id: d.id } },
          body: reason ? { reason_code: reason } : undefined,
        },
      );
      if (!response.ok) {
        const detail = (body as { detail?: unknown } | undefined)?.detail;
        setError(
          typeof detail === "string" ? detail : `HTTP ${response.status}`,
        );
      } else onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <li className="border-b border-border-soft px-5 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-sm font-medium">
          <span className="mr-2 font-mono text-xs font-semibold">
            {d.ticker ?? "—"}
          </span>
          {label}: {d.decision}
          <span className="num ml-2 text-xs font-normal text-text-muted">
            {confidenceLabel(d.confidence)}
          </span>
        </p>
        <span className="text-xs text-text-muted">
          {statusLabel(d.status)} · {deciderLabel(d.decided_by)} ·{" "}
          <time dateTime={d.decided_at}>
            {new Date(d.decided_at).toLocaleDateString()}
          </time>
          {d.review === "ok" ? " · checked, right" : null}
          {d.review === "wrong" ? " · checked, wrong" : null}
        </span>
      </div>
      <p className="mt-1 max-w-prose text-sm text-text-muted">{d.summary}</p>
      {canUndo(d) ? (
        <Button
          size="sm"
          variant="ghost"
          className="mt-1"
          disabled={pending}
          onClick={() => setAsking(true)}
        >
          <Undo2Icon aria-hidden="true" />
          Undo
        </Button>
      ) : null}
      {asking ? (
        <ReasonChips
          disabled={pending}
          onCancel={() => setAsking(false)}
          onPick={(reason) => void undo(reason)}
        />
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-sm text-loss">
          {error}
        </p>
      ) : null}
    </li>
  );
}

/** When a queue says it is 90% sure, how often is it right? One small table per queue. */
function CalibrationPanel({ load }: { load: Load<KindCalibration[]> }) {
  if (load.kind === "loading") return <Loading what="the calibration" />;
  if (load.kind === "error")
    return (
      <div className="p-5">
        <AlertMessage title="Could not load the calibration">
          {load.message}
        </AlertMessage>
      </div>
    );
  if (load.data.length === 0)
    return (
      <div className="p-5">
        <EmptyState title="Nothing checked yet">
          Once some of its decisions have been checked against your calls or a
          second model, each queue shows here how sure it was against how often
          it was right.
        </EmptyState>
      </div>
    );
  return (
    <div>
      {load.data.map((k) => (
        <section
          key={k.kind}
          aria-label={k.kind}
          className="border-b border-border-soft px-5 py-4 last:border-0"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <h3 className="text-[15px] font-medium">{k.kind}</h3>
            <span className="text-xs text-text-muted">
              {k.reviewed} checked
              {k.ece !== null
                ? ` · average gap ${Math.round(k.ece * 100)} points`
                : ""}
            </span>
          </div>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            {k.verdict}
          </p>
          <table className="mt-2 w-full max-w-md text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-text-muted">
                <th className="py-1 pr-3 font-medium">It said</th>
                <th className="num py-1 pr-3 text-right font-medium">
                  Checked
                </th>
                <th className="num py-1 pr-3 text-right font-medium">
                  Average sure
                </th>
                <th className="num py-1 text-right font-medium">Was right</th>
              </tr>
            </thead>
            <tbody>
              {k.bins.map((b) => (
                <tr key={b.low} className="border-b border-border-soft">
                  <td className="num py-1 pr-3">
                    {Math.round(b.low * 100)}–{Math.round(b.high * 100)}%
                  </td>
                  <td className="num py-1 pr-3 text-right">{b.n}</td>
                  <td className="num py-1 pr-3 text-right">
                    {Math.round(b.mean_confidence * 100)}%
                  </td>
                  <td className="num py-1 text-right">
                    {Math.round(b.share_right * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function KindsPanel({ load }: { load: Load<{ days: number; kinds: KindSummary[] }> }) {
  if (load.kind === "loading") return <Loading what="the queues" />;
  if (load.kind === "error")
    return (
      <div className="p-5">
        <AlertMessage title="Could not load the queues">
          {load.message}
        </AlertMessage>
      </div>
    );
  if (load.data.kinds.length === 0)
    return (
      <div className="p-5">
        <EmptyState title="No queues switched on">
          Queues appear here once the autopilot has a judge for them.
        </EmptyState>
      </div>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="px-5 py-3 text-left text-xs text-text-muted">
          Last {load.data.days} days. A queue is Watching until enough of its
          decisions have been checked against your calls and a second model;
          then it starts acting, and drops back if it gets worse.
        </caption>
        <thead>
          <tr className="border-y border-border text-xs text-text-muted">
            <th className="px-5 py-2 font-medium">Queue</th>
            <th className="px-3 py-2 font-medium">Mode</th>
            <th className="num px-3 py-2 text-right font-medium">Decided</th>
            <th className="num px-3 py-2 text-right font-medium">Applied</th>
            <th className="num px-3 py-2 text-right font-medium">Waiting</th>
            <th className="num px-3 py-2 text-right font-medium">Undone</th>
            <th className="px-5 py-2 font-medium">Accuracy</th>
          </tr>
        </thead>
        <tbody>
          {load.data.kinds.map((k) => (
            <tr key={k.kind} className="border-b border-border-soft">
              <td className="px-5 py-2">
                <Link href={k.href} className="hover:underline">
                  {k.label}
                </Link>
              </td>
              <td className="px-3 py-2" title={modeHelp(k.mode)}>
                {modeLabel(k.mode)}
              </td>
              <td className="num px-3 py-2 text-right">{k.decided}</td>
              <td className="num px-3 py-2 text-right">{k.applied}</td>
              <td className="num px-3 py-2 text-right">{k.waiting}</td>
              <td className="num px-3 py-2 text-right">{k.reverted}</td>
              <td className="px-5 py-2 text-text-muted">{precisionLabel(k)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
