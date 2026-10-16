import { CircleCheckIcon, CircleHelpIcon, CircleXIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/*
 * BulletRow: actual against target as a thin bar with a target tick, plus the figures in words.
 * Met or not met is always an icon and a word, never colour alone; the sentence on the right is
 * the table twin of the bar. `better` says which side of the target is good.
 */

export function BulletRow({
  label,
  actual,
  target,
  max,
  better = "higher",
  met,
  actualText,
  targetText,
  note,
}: {
  label: string;
  /** null = not enough data yet: the bar is empty and the row says so. */
  actual: number | null;
  target: number | null;
  /** Full scale of the bar; defaults to a little beyond the larger of actual and target. */
  max?: number;
  better?: "higher" | "lower";
  met: boolean | null;
  actualText: string;
  targetText: string;
  note?: string;
}) {
  const scale = max ?? Math.max(actual ?? 0, target ?? 0, 1e-9) * 1.25;
  const at = (v: number | null) => (v === null ? 0 : Math.max(0, Math.min(100, (v / scale) * 100)));
  const Icon = met === null ? CircleHelpIcon : met ? CircleCheckIcon : CircleXIcon;
  const word = met === null ? "Not enough data yet" : met ? "Met" : "Not met";
  return (
    <li className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border-soft py-2.5 first:border-t-0 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_minmax(0,16rem)] sm:items-center">
      <span className="text-sm">{label}</span>
      <div
        role="img"
        aria-label={`${label}: ${actualText}, ${better === "lower" ? "target under" : "target"} ${targetText}`}
        className="relative h-2 rounded-full bg-chart-track"
      >
        {actual !== null ? (
          <span
            className={cn("absolute inset-y-0 left-0 rounded-full", met === false ? "bg-warning-signal" : "bg-accent")}
            style={{ width: `${at(actual)}%` }}
          />
        ) : null}
        {target !== null ? <span aria-hidden="true" className="absolute -top-1 -bottom-1 w-0.5 bg-text" style={{ left: `${at(target)}%` }} /> : null}
      </div>
      <p className="flex flex-wrap items-center gap-x-1.5 text-sm sm:justify-end">
        <Icon aria-hidden="true" className={cn("size-4 shrink-0", met === null ? "text-text-faint" : met ? "text-gain" : "text-loss")} />
        <span className="num font-medium">{actualText}</span>
        <span className="text-xs text-text-muted">
          {better === "lower" ? "target under " : "target "}
          {targetText} · {word}
          {note ? ` · ${note}` : ""}
        </span>
      </p>
    </li>
  );
}
