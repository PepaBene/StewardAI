"use client";

import { CoinsIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useState, type ReactNode } from "react";

import { SaveNote, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { apiErrorText } from "@/lib/reference/form-utils";

type Settled<T = unknown> = { response: Response; error?: unknown; data?: T };

/**
 * Runs one mutation and tracks saving, success and error for a SaveNote. `okText` turns the
 * response body into the confirmation line; `onOk` is where callers refresh their data. The
 * body takes the call's generated response type, so a typed route needs no cast.
 */
export function useMutation() {
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  const run = useCallback(async <T = unknown,>(call: () => Promise<Settled<T>>, okText: (data: T) => string, onOk?: (data: T) => void) => {
    setState({ kind: "saving" });
    try {
      const r = await call();
      if (r.response.ok) {
        setState({ kind: "ok", text: okText(r.data as T) });
        onOk?.(r.data as T);
        return true;
      }
      setState({ kind: "error", text: apiErrorText(r.error, r.response.status) });
    } catch (err) {
      setState({ kind: "error", text: `Not saved: ${err instanceof Error ? err.message : String(err)}` });
    }
    return false;
  }, []);
  return { state, run, reset: () => setState({ kind: "idle" }) };
}

/**
 * A button for anything that spends free-tier provider credits. It says so before the click and
 * needs a second, explicit confirmation, so a stray click never uses quota.
 */
export function QuotaButton({
  label,
  provider,
  cost,
  busy,
  onConfirm,
  disabled,
}: {
  label: string;
  provider: string;
  /** What it spends, e.g. "1 request of the daily free quota". */
  cost?: string;
  busy?: boolean;
  onConfirm: () => void;
  disabled?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={disabled || busy} onClick={() => setArmed(true)}>
          <CoinsIcon aria-hidden="true" className="size-3.5" />
          {label}
        </Button>
        <span className="text-xs text-text-muted">Spends {provider} free-tier credits{cost ? `: ${cost}` : ""}.</span>
      </div>
    );
  }
  return (
    <div role="group" aria-label={`Confirm ${label}`} className="flex flex-wrap items-center gap-2 border border-dashed border-border px-3 py-2">
      <span className="max-w-[56ch] text-sm">
        This calls {provider} and spends free-tier credits{cost ? ` (${cost})` : ""}. Nothing is retried automatically.
      </span>
      <Button
        type="button"
        size="sm"
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {label}
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={() => setArmed(false)}>
        Cancel
      </Button>
    </div>
  );
}

/** The job a trigger queued, with a link to its page. */
export function QueuedJob({ data }: { data: unknown }) {
  const id = typeof data === "object" && data !== null && "id" in data ? Number((data as { id: unknown }).id) : NaN;
  const status = typeof data === "object" && data !== null && "status" in data ? String((data as { status: unknown }).status) : null;
  if (!Number.isFinite(id)) return null;
  return (
    <p className="text-sm">
      Job{" "}
      <Link href={`/jobs/${id}`} className="font-mono text-accent-ink hover:underline">
        #{id}
      </Link>
      {status ? <span className="text-text-muted"> is {status}.</span> : null}
    </p>
  );
}

/** A small key/value table of a record, for untyped responses. Missing fields read "Unknown". */
export function KeyRows({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,12rem)_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map((r) => (
        <div key={r.label} className="contents">
          <dt className="text-text-muted">{r.label}</dt>
          <dd className="min-w-0 break-words">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export { SaveNote };
