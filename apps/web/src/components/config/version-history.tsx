"use client";

import {
  ArrowRightIcon,
  CheckIcon,
  LoaderCircleIcon,
  MinusIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type ConfigDiff,
  type ConfigDomainSlug,
  type ConfigVersionBody,
  type ConfigVersionMeta,
  type DiffChange,
} from "@/lib/api/client";
import { CONFIG_DOMAINS } from "@/lib/config/domains";
import {
  defaultFrom,
  formatPath,
  formatValue,
  initialSelection,
  listErrors,
  OP_LABELS,
  type ListedError,
} from "@/lib/config/history";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const api = () => getBrowserApiClient();

type Load<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; data: T };

function httpMessage(status: number, error: unknown): string {
  const detail = error && typeof error === "object" && "detail" in error ? error.detail : null;
  return typeof detail === "string" ? detail : `The API answered HTTP ${status}.`;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

type RollbackState =
  | { kind: "idle" }
  | { kind: "confirm"; id: number }
  | { kind: "busy"; id: number }
  | { kind: "done"; from: number; version: ConfigVersionBody }
  | { kind: "invalid"; id: number; errors: ListedError[] }
  | { kind: "error"; message: string };

/**
 * Every saved version of one config domain: pick any two to diff (by default the selected
 * version against its parent), read a version's JSON, and roll back to an old version (its
 * body saved as a new active version, after a confirm step).
 */
export function VersionHistory({
  domain,
  initialVersion = null,
}: {
  domain: ConfigDomainSlug;
  /** Select this version first (e.g. "View diff" from an editor), when it is in the list. */
  initialVersion?: number | null;
}) {
  const info = CONFIG_DOMAINS[domain];
  const [versions, setVersions] = useState<Load<ConfigVersionMeta[]>>({ kind: "loading" });
  const [selected, setSelected] = useState<number | null>(null);
  const [fromId, setFromId] = useState<number | null>(null);
  const [view, setView] = useState<"changes" | "json">("changes");
  const [rollback, setRollback] = useState<RollbackState>({ kind: "idle" });

  const loadVersions = useCallback(
    async (select?: number) => {
      try {
        const { data, error, response } = await api().GET("/config/{domain}/versions", {
          params: { path: { domain } },
        });
        if (!data) {
          setVersions({ kind: "error", message: httpMessage(response.status, error) });
          return;
        }
        setVersions({ kind: "ok", data });
        const pick =
          select ?? (initialVersion !== null && data.some((v) => v.id === initialVersion) ? initialVersion : initialSelection(data));
        setSelected(pick);
        setFromId(pick === null ? null : defaultFrom(data, pick));
      } catch (err) {
        setVersions({ kind: "error", message: errorMessage(err) });
      }
    },
    [domain, initialVersion],
  );

  useEffect(() => {
    // Fetch on mount; the state updates happen after the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadVersions();
  }, [loadVersions]);

  const list = versions.kind === "ok" ? versions.data : [];
  const current = list.find((v) => v.id === selected) ?? null;

  function select(id: number) {
    setSelected(id);
    setFromId(defaultFrom(list, id));
    if (rollback.kind === "confirm" || rollback.kind === "invalid" || rollback.kind === "error") setRollback({ kind: "idle" });
  }

  async function confirmRollback(id: number) {
    setRollback({ kind: "busy", id });
    try {
      const { data, error, response } = await api().POST("/config/{domain}/versions/{version_id}/rollback", {
        params: { path: { domain, version_id: id } },
      });
      if (data) {
        setRollback({ kind: "done", from: id, version: data });
        await loadVersions(data.id);
      } else if (response.status === 409 || response.status === 422) {
        setRollback({ kind: "invalid", id, errors: listErrors(error) });
      } else {
        setRollback({ kind: "error", message: httpMessage(response.status, error) });
      }
    } catch (err) {
      setRollback({ kind: "error", message: errorMessage(err) });
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        kicker={
          <>
            <Link href="/config" className="hover:underline">
              Config
            </Link>
            {info.editor ? (
              <>
                {" · "}
                <Link href={info.editor} className="hover:underline">
                  {info.label}
                </Link>
              </>
            ) : null}
          </>
        }
        title={`${info.label}: version history`}
        description={
          <p className="max-w-prose">
            Every save is a new version; nothing is overwritten. Compare any two versions, or roll back: the old
            version&rsquo;s body is saved again as a new active version, and every job uses it from its next run.
          </p>
        }
        aside={
          info.editor ? (
            <Button asChild variant="outline" size="sm">
              <Link href={info.editor}>
                <PencilIcon aria-hidden="true" />
                Open editor
              </Link>
            </Button>
          ) : (
            <span className="text-sm text-text-muted">{info.note}</span>
          )
        }
      />

      <DomainSwitcher current={domain} />

      {versions.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading versions…
        </p>
      ) : null}
      {versions.kind === "error" ? (
        <AlertMessage title="Could not load the versions">
          <p>{versions.message}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => void loadVersions()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      <RollbackResult state={rollback} domain={domain} />

      {versions.kind === "ok" && list.length === 0 ? (
        <EmptyState title="No versions yet">
          Nothing has been saved for {info.label.toLowerCase()}; the backend uses its defaults.
        </EmptyState>
      ) : null}

      {versions.kind === "ok" && list.length > 0 ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
          <VersionList versions={list} selected={selected} onSelect={select} />
          {current ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-[22px] leading-tight font-semibold">
                  Version <span className="num">#{current.id}</span>
                  {current.active ? <ActiveBadge className="ml-2 align-middle" /> : null}
                </h2>
                {!current.active ? (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={rollback.kind === "busy"}
                    onClick={() => setRollback({ kind: "confirm", id: current.id })}
                  >
                    <RotateCcwIcon aria-hidden="true" />
                    Roll back to #{current.id}
                  </Button>
                ) : null}
              </div>
              {current.note ? <p className="text-sm text-text-muted">&ldquo;{current.note}&rdquo;</p> : null}

              {(rollback.kind === "confirm" || rollback.kind === "busy") && rollback.id === current.id ? (
                <ConfirmRollback
                  id={current.id}
                  label={info.label}
                  busy={rollback.kind === "busy"}
                  onConfirm={() => void confirmRollback(current.id)}
                  onCancel={() => setRollback({ kind: "idle" })}
                />
              ) : null}
              {rollback.kind === "invalid" && rollback.id === current.id ? (
                <AlertMessage title={`Version #${rollback.id} is no longer valid, so it was not restored`}>
                  <p>The current schema rejects its body. Nothing was saved. Fix these in the editor instead:</p>
                  <ErrorList errors={rollback.errors} />
                </AlertMessage>
              ) : null}

              <div role="tablist" aria-label="Version view" className="flex gap-1 border-b border-border">
                {(["changes", "json"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={view === tab}
                    onClick={() => setView(tab)}
                    className={cn(
                      "-mb-px h-9 border-b-2 px-3 font-heading text-[15px] font-semibold",
                      view === tab ? "border-accent font-medium text-text" : "border-transparent text-text-muted hover:text-text",
                    )}
                  >
                    {tab === "changes" ? "Changes" : "JSON"}
                  </button>
                ))}
              </div>
              {view === "changes" ? (
                <DiffPanel
                  domain={domain}
                  versions={list}
                  fromId={fromId}
                  toId={current.id}
                  onFrom={setFromId}
                  onTo={(id) => {
                    setSelected(id);
                    setRollback({ kind: "idle" });
                  }}
                />
              ) : (
                <JsonPanel domain={domain} id={current.id} />
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function DomainSwitcher({ current }: { current: ConfigDomainSlug }) {
  return (
    <nav aria-label="Config domains">
      <ul className="flex flex-wrap gap-1">
        {(Object.keys(CONFIG_DOMAINS) as ConfigDomainSlug[]).map((slug) => (
          <li key={slug}>
            <Link
              href={`/config/history/${slug}`}
              aria-current={slug === current ? "page" : undefined}
              className={cn(
                "inline-flex h-8 items-center border px-2.5 text-sm",
                slug === current
                  ? "border-accent bg-accent-soft font-medium text-accent-ink"
                  : "border-border text-text-muted hover:text-text",
              )}
            >
              {CONFIG_DOMAINS[slug].label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ActiveBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 bg-accent-soft px-1.5 py-0.5 text-xs font-semibold text-accent-ink",
        className,
      )}
    >
      <CheckIcon aria-hidden="true" className="size-3.5" />
      Active
    </span>
  );
}

function VersionList({
  versions,
  selected,
  onSelect,
}: {
  versions: ConfigVersionMeta[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="overflow-hidden border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">Saved versions, newest first. Choose one to see its changes.</caption>
        <thead className="bg-surface-muted text-[11px] tracking-[0.08em] uppercase">
          <tr className="border-b border-border text-text-muted">
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Version
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Changes
            </th>
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => {
            const isSelected = v.id === selected;
            return (
              <tr key={v.id} className={cn("border-b border-border last:border-0", isSelected && "bg-surface-muted")}>
                <th scope="row" className="px-3 py-2 text-left font-normal">
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => onSelect(v.id)}
                    className="block w-full rounded-sm text-left"
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className={cn("num font-medium", isSelected ? "text-accent-ink" : "text-text")}>#{v.id}</span>
                      {v.active ? <ActiveBadge /> : null}
                      <span className="num text-xs text-text-muted">{formatDateTime(v.created_at)}</span>
                    </span>
                    {v.note ? <span className="mt-0.5 block truncate text-xs text-text-muted">{v.note}</span> : null}
                  </button>
                </th>
                <td className="num px-3 py-2 text-right align-top text-text-muted">
                  {v.changes === null ? <span title="First version">first</span> : v.changes}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ConfirmRollback({
  id,
  label,
  busy,
  onConfirm,
  onCancel,
}: {
  id: number;
  label: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div role="alertdialog" aria-labelledby="rollback-title" aria-describedby="rollback-body" className="space-y-2 rounded-lg border border-warning-signal/40 bg-warning-signal-subtle p-3">
      <p id="rollback-title" className="font-medium">
        Roll back {label.toLowerCase()} to version #{id}?
      </p>
      <p id="rollback-body" className="text-sm text-text-muted">
        Its body is saved as a new active version (history is kept, and you can roll forward the
        same way). Jobs use it from their next run.
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onConfirm} disabled={busy} autoFocus>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RotateCcwIcon aria-hidden="true" />}
          Roll back
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function RollbackResult({ state, domain }: { state: RollbackState; domain: ConfigDomainSlug }) {
  const info = CONFIG_DOMAINS[domain];
  if (state.kind === "error") return <AlertMessage title="Roll back failed">{state.message}</AlertMessage>;
  if (state.kind !== "done") return null;
  return (
    <AlertMessage tone="info" title={`Rolled back: version #${state.version.id} is now active`}>
      <p>
        It is a copy of version #{state.from}.{" "}
        {domain === "strategy" ? (
          <>
            Drift is computed against it now.{" "}
            <Link href="/" className="inline-flex items-center gap-1 text-accent-ink hover:underline">
              See the Overview drift
              <ArrowRightIcon aria-hidden="true" className="size-3.5" />
            </Link>
          </>
        ) : info.editor ? (
          <Link href={info.editor} className="inline-flex items-center gap-1 text-accent-ink hover:underline">
            Open the {info.label.toLowerCase()} editor
            <ArrowRightIcon aria-hidden="true" className="size-3.5" />
          </Link>
        ) : null}
      </p>
    </AlertMessage>
  );
}

function ErrorList({ errors }: { errors: ListedError[] }) {
  if (errors.length === 0) return <p>The API gave no details.</p>;
  return (
    <ul className="mt-1 list-disc space-y-0.5 pl-4">
      {errors.map((e, i) => (
        <li key={`${i}:${e.path}`}>
          {e.path ? <span className="font-mono text-xs">{e.path}</span> : null} {e.message}
        </li>
      ))}
    </ul>
  );
}

function versionOption(v: ConfigVersionMeta): string {
  return `#${v.id} · ${formatDateTime(v.created_at)}${v.active ? " · active" : ""}${v.note ? ` · ${v.note.slice(0, 40)}` : ""}`;
}

const OP_ICONS: Record<DiffChange["op"], typeof PlusIcon> = { add: PlusIcon, remove: MinusIcon, replace: PencilIcon };

function DiffPanel({
  domain,
  versions,
  fromId,
  toId,
  onFrom,
  onTo,
}: {
  domain: ConfigDomainSlug;
  versions: ConfigVersionMeta[];
  fromId: number | null;
  toId: number;
  onFrom: (id: number | null) => void;
  onTo: (id: number) => void;
}) {
  const fromSelect = useId();
  const toSelect = useId();
  const [diff, setDiff] = useState<Load<ConfigDiff> | null>(null);

  useEffect(() => {
    if (fromId === null || fromId === toId) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDiff({ kind: "loading" });
    api()
      .GET("/config/{domain}/diff", { params: { path: { domain }, query: { from: fromId, to: toId } } })
      .then(({ data, error, response }) => {
        if (cancelled) return;
        setDiff(data ? { kind: "ok", data } : { kind: "error", message: httpMessage(response.status, error) });
      })
      .catch((err: unknown) => {
        if (!cancelled) setDiff({ kind: "error", message: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [domain, fromId, toId]);

  const selectClass =
    "h-9 w-full border border-border px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={fromSelect}>From (older)</Label>
          <select
            id={fromSelect}
            className={selectClass}
            value={fromId ?? ""}
            onChange={(e) => onFrom(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">Nothing (first version)</option>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionOption(v)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor={toSelect}>To</Label>
          <select id={toSelect} className={selectClass} value={toId} onChange={(e) => onTo(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {versionOption(v)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {fromId === null ? (
        <p className="text-sm text-text-muted">
          Nothing to compare with: pick a version under &ldquo;From&rdquo;, or open the JSON tab to
          read this version in full.
        </p>
      ) : fromId === toId ? (
        <p className="text-sm text-text-muted">Both sides are the same version.</p>
      ) : diff === null || diff.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Comparing…
        </p>
      ) : diff.kind === "error" ? (
        <AlertMessage title="Could not compare the versions">{diff.message}</AlertMessage>
      ) : diff.data.changes.length === 0 ? (
        <p className="text-sm text-text-muted">
          No differences between #{diff.data.from_id} and #{diff.data.to_id}.
        </p>
      ) : (
        <div className="overflow-x-auto border border-border">
          <table className="w-full min-w-[36rem] text-sm">
            <caption className="sr-only">
              {diff.data.changes.length} changes from version {diff.data.from_id} to version {diff.data.to_id}
            </caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-left text-text-muted">
                <th scope="col" className="px-3 py-2 font-medium">
                  Field
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Change
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Before (#{diff.data.from_id})
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  After (#{diff.data.to_id})
                </th>
              </tr>
            </thead>
            <tbody>
              {diff.data.changes.map((c, i) => {
                const Icon = OP_ICONS[c.op];
                return (
                  <tr key={`${i}:${c.path}`} className="border-b border-border align-top last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-mono text-xs font-normal break-all">
                      {formatPath(c.path)}
                    </th>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1">
                        <Icon aria-hidden="true" className="size-3.5 text-text-muted" />
                        {OP_LABELS[c.op]}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <JsonValue value={c.op === "add" ? undefined : c.old} />
                    </td>
                    <td className="px-3 py-2">
                      <JsonValue value={c.op === "remove" ? undefined : c.new} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function JsonValue({ value }: { value: unknown }) {
  return (
    <pre className="num max-h-48 max-w-[28rem] overflow-auto font-mono text-xs whitespace-pre-wrap break-all text-text">
      {formatValue(value)}
    </pre>
  );
}

function JsonPanel({ domain, id }: { domain: ConfigDomainSlug; id: number }) {
  const [state, setState] = useState<Load<ConfigVersionBody>>({ kind: "loading" });
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ kind: "loading" });
    api()
      .GET("/config/{domain}/versions/{version_id}", { params: { path: { domain, version_id: id } } })
      .then(({ data, error, response }) => {
        if (!cancelled) setState(data ? { kind: "ok", data } : { kind: "error", message: httpMessage(response.status, error) });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [domain, id]);

  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading version #{id}…
      </p>
    );
  }
  if (state.kind === "error") return <AlertMessage title={`Could not load version #${id}`}>{state.message}</AlertMessage>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-text-muted">
        Saved {formatDateTime(state.data.created_at)} · schema version {state.data.schema_version}
        {state.data.parent_id !== null ? ` · parent #${state.data.parent_id}` : ""}
      </p>
      <pre className="num max-h-[36rem] overflow-auto rounded-lg border border-border bg-surface-muted p-3 font-mono text-xs text-text">
        {JSON.stringify(state.data.body, null, 2)}
      </pre>
    </div>
  );
}
