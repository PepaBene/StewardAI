"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import type { SavedItem, SavedKind } from "@/lib/risk/risk";
import { cn } from "@/lib/utils";

/** The message the API sent with a failed call, or a plain fallback. */
export function problemText(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string" && detail !== "") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
  }
  return fallback;
}

export function Problem({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p role="alert" className="border-l-2 border-warning-signal bg-warning-signal/5 px-3 py-2 text-sm">
      {children}
    </p>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1 text-sm", className)}>
      <span className="font-medium">{label}</span>
      {children}
      {hint ? <span className="text-xs text-text-muted">{hint}</span> : null}
    </label>
  );
}

export const TEXTAREA =
  "min-h-20 w-full border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

export const SELECT =
  "h-9 w-full border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

/** Name it and keep it. `save` runs the server side save and resolves to an error message or null. */
export function SaveBar({ save, onSaved, label = "Save this" }: { save: (name: string) => Promise<string | null>; onSaved: () => void; label?: string }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function go() {
    if (name.trim() === "") return setProblem("Give it a name first.");
    setBusy(true);
    setProblem(null);
    setDone(false);
    const err = await save(name.trim());
    setBusy(false);
    if (err) return setProblem(err);
    setName("");
    setDone(true);
    onSaved();
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Name" className="w-64 max-w-full">
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. Equities down 20%" />
        </Field>
        <Button type="button" variant="outline" onClick={go} disabled={busy}>
          {busy ? "Saving…" : label}
        </Button>
        {done ? <span className="pb-2 text-sm text-text-muted">Saved. It is kept as it stands today.</span> : null}
      </div>
      <Problem>{problem}</Problem>
    </div>
  );
}

/** What was kept: open one to see it exactly as it was, or remove it from the list. */
export function SavedList({ kind, refreshKey, onOpen }: { kind: SavedKind; refreshKey: number; onOpen: (payload: { request: unknown; result: unknown }, name: string) => void }) {
  const [items, setItems] = useState<SavedItem[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await getBrowserApiClient().GET("/portfolio/decision/saved", { params: { query: { kind } } });
    if (res.response.ok && res.data) setItems(res.data);
    else setProblem("The saved list could not be loaded.");
  }, [kind]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads the list on mount and after a save
    void load();
  }, [load, refreshKey]);

  async function open(item: SavedItem) {
    const res = await getBrowserApiClient().GET("/portfolio/decision/saved/{item_id}", {
      params: { path: { item_id: item.id }, query: { kind } },
    });
    if (res.response.ok && res.data) onOpen(res.data.payload as { request: unknown; result: unknown }, item.name);
    else setProblem("That item could not be opened.");
  }

  async function remove(item: SavedItem) {
    const res = await getBrowserApiClient().DELETE("/portfolio/decision/saved/{item_id}", {
      params: { path: { item_id: item.id }, query: { kind } },
    });
    if (!res.response.ok) return setProblem("That item could not be removed.");
    await load();
  }

  if (items === null) return problem ? <Problem>{problem}</Problem> : null;
  if (items.length === 0) return <p className="text-sm text-text-muted">Nothing saved yet. Run one, then name it to keep it.</p>;
  return (
    <div className="space-y-2">
      <Problem>{problem}</Problem>
      <ul className="divide-y divide-border-soft border border-border-soft">
        {items.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
            <div className="min-w-0">
              <span className="font-medium">{item.name}</span>
              <span className="ml-2 text-xs text-text-muted">saved {formatDate(item.created_at)}</span>
            </div>
            <div className="flex gap-1">
              <Button type="button" size="sm" variant="ghost" onClick={() => open(item)}>
                Open as saved
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => remove(item)} aria-label={`Remove ${item.name}`}>
                Remove
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
