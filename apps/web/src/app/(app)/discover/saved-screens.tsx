"use client";

import { BookmarkIcon, PlayIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";

import { ScoreMark } from "@/components/insight/score-mark";
import { Button } from "@/components/ui/button";
import { petalsFromMap } from "@/lib/insight/insight";
import { Input } from "@/components/ui/input";
import type { ColumnSet, ScreenSummary } from "@/lib/discover/discover";

/** Saved screens and named column sets. A changed definition saves a new version. */
export function SavedScreens({
  screens,
  columnSets,
  activeId,
  onSave,
  onLoad,
  onRun,
  onDelete,
  onSaveColumns,
  onLoadColumns,
  busy,
  canSave,
}: {
  screens: readonly ScreenSummary[];
  columnSets: readonly ColumnSet[];
  activeId: number | null;
  onSave: (name: string) => void;
  onLoad: (id: number) => void;
  onRun: (id: number) => void;
  onDelete: (id: number) => void;
  onSaveColumns: (name: string) => void;
  onLoadColumns: (id: number) => void;
  busy: boolean;
  canSave: boolean;
}) {
  const [name, setName] = useState("");
  const [setName_, setSetName] = useState("");
  const active = screens.find((s) => s.id === activeId);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Saved screens</h3>
        {screens.length === 0 ? (
          <p className="text-xs text-text-muted">Nothing saved yet. Set conditions, name them and save: each later change adds a version, and earlier runs keep theirs.</p>
        ) : (
          <ul className="space-y-1">
            {screens.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-1 text-sm">
                <button
                  type="button"
                  onClick={() => onLoad(s.id)}
                  aria-current={s.id === activeId}
                  className={`px-1 text-left hover:text-accent-ink hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none ${s.id === activeId ? "font-semibold" : ""}`}
                >
                  {s.name}
                </button>
                <span className="num text-xs text-text-muted">v{s.latest_version}</span>
                {s.mark ? <ScoreMark petals={petalsFromMap(s.mark)} label={`${s.name}, mean of ${s.last_matched ?? 0} matches`} /> : null}
                {s.new_last_7d !== null && s.new_last_7d !== undefined ? (
                  <span
                    className={`num border px-1.5 text-[11px] ${s.new_last_7d > 0 ? "border-accent text-accent-ink" : "border-border text-text-muted"}`}
                    title={`${s.new_basis ?? ""}${s.new_tickers?.length ? `: ${s.new_tickers.join(", ")}` : ""}`}
                  >
                    {s.new_last_7d} new in 7 days
                  </span>
                ) : s.last_run_at ? (
                  <span className="text-[11px] text-text-faint" title={s.new_basis ?? undefined}>
                    {s.last_matched ?? 0} matches
                  </span>
                ) : null}
                <Button type="button" variant="ghost" size="sm" aria-label={`Run ${s.name}`} onClick={() => onRun(s.id)} disabled={busy}>
                  <PlayIcon aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" size="sm" aria-label={`Archive ${s.name}`} onClick={() => onDelete(s.id)} disabled={busy}>
                  <Trash2Icon aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim() || active?.name || "";
            if (value) onSave(value);
          }}
        >
          <Input aria-label="Screen name" placeholder={active ? active.name : "Name this screen"} value={name} onChange={(e) => setName(e.target.value)} className="w-56" />
          <Button type="submit" variant="outline" size="sm" disabled={busy || !canSave}>
            <BookmarkIcon aria-hidden="true" />
            {active && !name.trim() ? "Save new version" : "Save screen"}
          </Button>
        </form>
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Column sets</h3>
        {columnSets.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">
            {columnSets.map((c) => (
              <li key={c.id ?? c.name}>
                <button
                  type="button"
                  onClick={() => c.id !== null && c.id !== undefined && onLoadColumns(c.id)}
                  className="border border-border px-2 py-1 text-xs hover:bg-border-soft focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                >
                  {c.name}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (setName_.trim()) onSaveColumns(setName_.trim());
          }}
        >
          <Input aria-label="Column set name" placeholder="Name the current columns" value={setName_} onChange={(e) => setSetName(e.target.value)} className="w-56" />
          <Button type="submit" variant="outline" size="sm" disabled={busy || !setName_.trim()}>
            Save columns
          </Button>
        </form>
      </div>
    </div>
  );
}
