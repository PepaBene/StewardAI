"use client";

import { LoaderCircleIcon, RefreshCwIcon, Undo2Icon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { GatesPanel } from "./gates-panel";

type Entry = components["schemas"]["Entry-Input"];
type Sweep = components["schemas"]["SweepOut"];
type ActionRow = components["schemas"]["ActionRow"];

const PAGE = 50;

const SOURCE_LABELS: Record<string, string> = {
  autopilot: "Autopilot decisions",
  watchdog: "Watchdog",
  owner_action: "Your changes",
  engine_change: "Proposed changes",
  config: "Settings it saved",
  override: "Your corrections",
  delivery: "Alert delivery",
  trigger: "Proactive runs",
  journal: "Journal",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

function whenLabel(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** One list of everything the engine or you changed, with an undo where one exists. */
export function LedgerView() {
  const api = getBrowserApiClient();
  const [source, setSource] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [sweeps, setSweeps] = useState<Sweep[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEntries(null);
    api
      .GET("/engine/ledger", {
        params: {
          query: {
            days: 90,
            limit: PAGE,
            offset,
            ...(source ? { source } : {}),
          },
        },
      })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) {
          setEntries(data);
          setError(null);
        } else setError(`The API answered HTTP ${response.status}.`);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
    // The client is rebuilt each render; these say when to fetch again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, offset, reloadKey]);

  useEffect(() => {
    api
      .GET("/engine/sweeps")
      .then(({ data }) => setSweeps(data ?? []))
      .catch(() => setSweeps([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  const last = sweeps[0];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="What the engine did"
        description="Every change the engine made on its own, and every change you made, in one list. Where a change can be reversed, there is an undo."
        aside={
          <Button variant="ghost" size="sm" onClick={reload}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />

      {last ? (
        <p className="max-w-prose text-sm text-text-muted">
          Weekly recheck of applied decisions: {last.flagged} of {last.checked}{" "}
          no longer held
          {last.disagreement_rate !== null
            ? ` (${Math.round(last.disagreement_rate * 100)}%)`
            : ""}
          , on {whenLabel(last.ran_at)}.
        </p>
      ) : null}

      <GatesPanel />

      <Blueprint aria-labelledby="ledger-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
          <h2
            id="ledger-heading"
            className="text-[22px] leading-tight font-semibold"
          >
            {source ? sourceLabel(source) : "Everything"}
          </h2>
          <nav
            aria-label="Show"
            className="flex flex-wrap border border-border"
          >
            {[null, ...Object.keys(SOURCE_LABELS)].map((s) => (
              <button
                key={s ?? "all"}
                type="button"
                aria-pressed={source === s}
                onClick={() => {
                  setSource(s);
                  setOffset(0);
                }}
                className={cn(
                  "border-l border-border px-3 py-1.5 text-xs first:border-l-0",
                  source === s
                    ? "bg-accent text-accent-foreground"
                    : "text-text hover:bg-border-soft",
                )}
              >
                {s ? sourceLabel(s) : "Everything"}
              </button>
            ))}
          </nav>
        </div>

        {error ? (
          <div className="p-5">
            <AlertMessage title="Could not load the list">{error}</AlertMessage>
          </div>
        ) : entries === null ? (
          <p
            className="flex items-center gap-2 px-5 py-4 text-sm text-text-muted"
            role="status"
          >
            <LoaderCircleIcon
              aria-hidden="true"
              className="size-4 animate-spin"
            />
            Loading the list…
          </p>
        ) : entries.length === 0 ? (
          <div className="p-5">
            <EmptyState title="Nothing here">
              {offset > 0
                ? "There are no older entries."
                : "No changes in the last 90 days."}
            </EmptyState>
          </div>
        ) : (
          <ul className="m-0 list-none p-0">
            {entries.map((e) => (
              <EntryRow
                key={`${e.source}-${e.ref}-${e.at}`}
                entry={e}
                onChanged={reload}
              />
            ))}
          </ul>
        )}

        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <Button
            size="sm"
            variant="outline"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            Newer
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={entries === null || entries.length < PAGE}
            onClick={() => setOffset(offset + PAGE)}
          >
            Older
          </Button>
        </div>
      </Blueprint>
    </div>
  );
}

function EntryRow({
  entry,
  onChanged,
}: {
  entry: Entry;
  onChanged: () => void;
}) {
  const api = getBrowserApiClient();
  const [proposal, setProposal] = useState<ActionRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function guard(step: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await step();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function failure(body: unknown, status: number): string {
    const detail = (body as { detail?: unknown } | undefined)?.detail;
    return typeof detail === "string" ? detail : `HTTP ${status}`;
  }

  const propose = () =>
    guard(async () => {
      const { data, response, error: body } = await api.POST(
        "/actions/propose",
        {
          body: {
            action: entry.undo_action as string,
            args: entry.undo_args ?? {},
            via: "screen",
          },
        },
      );
      if (!data) setError(failure(body, response.status));
      else setProposal(data);
    });

  const confirm = () =>
    guard(async () => {
      if (!proposal) return;
      const { response, error: body } = await api.POST(
        "/actions/{action_id}/confirm",
        {
          params: { path: { action_id: proposal.id } },
          body: { second: proposal.risk === "high" },
        },
      );
      if (!response.ok) setError(failure(body, response.status));
      else {
        setProposal(null);
        onChanged();
      }
    });

  const cancel = () =>
    guard(async () => {
      if (!proposal) return;
      await api.POST("/actions/{action_id}/cancel", {
        params: { path: { action_id: proposal.id } },
      });
      setProposal(null);
    });

  return (
    <li className="border-b border-border-soft px-5 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="max-w-prose text-sm">{entry.summary}</p>
        <span className="text-xs text-text-muted">
          {sourceLabel(entry.source)} · {whenLabel(entry.at)}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-text-muted">{entry.status}</p>

      {entry.undo_action && !proposal ? (
        <div className="mt-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={propose}
          >
            <Undo2Icon aria-hidden="true" />
            Undo
          </Button>
        </div>
      ) : null}

      {proposal ? (
        <div className="mt-2 flex flex-col gap-2 border border-border-soft p-3">
          <p className="max-w-prose text-sm">{proposal.summary}</p>
          <p className="text-xs text-text-muted">{proposal.undo}</p>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={confirm}>
              Confirm undo
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={cancel}
            >
              Keep it
            </Button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="mt-2 text-sm text-loss">
          {error}
        </p>
      ) : null}
    </li>
  );
}
