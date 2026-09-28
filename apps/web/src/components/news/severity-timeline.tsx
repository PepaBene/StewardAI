import { ChartEmpty, Legend, SrTable } from "@/components/charts";
import { StatusDot } from "@/components/ui/status-dot";
import { SEVERITY, type SeverityLevel } from "@/lib/news/labels";
import { severityBlocks, type SeverityColumn } from "@/lib/news/stats";
import { cn } from "@/lib/utils";

/**
 * A severity timeline's marker: the same neutral/info/warning tone SeverityBadge uses, so
 * a dot and its badge always agree. Decorative; the row's text and SeverityBadge remain the
 * accessible source of truth.
 */
export function SeverityDot({ severity, className }: { severity: number | null; className?: string }) {
  const level = severity === null ? null : (Math.max(0, Math.min(3, severity)) as SeverityLevel);
  const tone = level === null ? "neutral" : SEVERITY[level].tone;
  return <StatusDot tone={tone as "neutral" | "info" | "warning"} className={className} />;
}

/**
 * The story row's severity marker: three blocks filled up to the level (3 = alert,
 * 2 = highlight, 1 = digest) and the level's word, so the level never rests on colour.
 */
export function SeverityBlocks({ severity, className }: { severity: number | null; className?: string }) {
  const filled = severityBlocks(severity);
  const level = severity === null ? null : (Math.max(0, Math.min(3, severity)) as SeverityLevel);
  return (
    <span className={cn("inline-flex items-center gap-[3px]", className)} title={level === null ? "Not classified" : SEVERITY[level].description}>
      {[0, 1, 2].map((i) => (
        <span key={i} aria-hidden="true" className={cn("h-3.5 w-2", i < filled ? "bg-accent" : "bg-border")} />
      ))}
      <span className="ml-1.5 text-xs text-text">
        <span className="sr-only">Severity {level ?? "unrated"}: </span>
        {level === null ? "Unrated" : SEVERITY[level].label}
      </span>
    </span>
  );
}

const BANDS = [
  { key: "alert", label: "Alert (3)", className: "bg-accent" },
  { key: "highlight", label: "Highlight (2)", className: "bg-accent-300" },
  { key: "digest", label: "Digest (1)", className: "bg-accent-200" },
  { key: "other", label: "Ignored or unclassified", className: "bg-border" },
] as const;

/** Stories per day, stacked by severity band (alert at the bottom, as in the design). */
export function SeverityTimeline({ columns, height = 150 }: { columns: readonly SeverityColumn[]; height?: number }) {
  const max = Math.max(0, ...columns.map((c) => c.total));
  if (columns.length === 0 || max === 0) return <ChartEmpty label="No stories in this window" minHeight={height} />;
  const px = (n: number) => (n / max) * height;
  return (
    <div className="flex flex-col gap-2">
      <Legend items={BANDS.map((b) => ({ label: b.label, className: b.className }))} />
      <div aria-hidden="true" className="flex items-end gap-1.5" style={{ height }}>
        {columns.map((c) => (
          <div
            key={c.date}
            className="flex h-full min-w-0 flex-1 flex-col-reverse gap-px"
            title={`${c.date}: ${c.total} ${c.total === 1 ? "story" : "stories"} (${c.alert} alert, ${c.highlight} highlight, ${c.digest} digest, ${c.other} other)`}
          >
            {BANDS.map((b) => (c[b.key] > 0 ? <div key={b.key} className={b.className} style={{ height: px(c[b.key]) }} /> : null))}
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="flex gap-1.5">
        {columns.map((c, i) => (
          <span key={c.date} className="num min-w-0 flex-1 text-center font-mono text-[10px] text-text-faint">
            {columns.length > 16 && i % 2 === 1 ? "" : c.label}
          </span>
        ))}
      </div>
      <SrTable
        caption="Stories per day by severity"
        columns={["Day", ...BANDS.map((b) => b.label), "Total"]}
        rows={columns.map((c) => [c.date, c.alert, c.highlight, c.digest, c.other, c.total])}
      />
    </div>
  );
}
