import type { ReactNode } from "react";

import { type Verdict, VerdictLine } from "@/components/insight/verdict-line";
import { cn } from "@/lib/utils";

/*
 * F36 KpiTile and KpiStrip (visual spec B2). A caption label, the figure in the serif, an
 * optional signed change line, an optional one-sentence verdict, an optional sparkline.
 * Tiles sit in one card separated by hairlines: four across on desktop, 2×2 below md.
 */

export type KpiChange = {
  /** Signed value already formatted, e.g. "+0.84%" or "−CZK 71". */
  text: string;
  direction: "up" | "down" | "flat";
  /** "today", "vs last 12 months"… */
  period?: string;
};

export type KpiTileProps = {
  label: string;
  value: ReactNode;
  change?: KpiChange;
  verdict?: { tone: Verdict; text: ReactNode };
  /** A chip after the label (Badge). */
  badge?: ReactNode;
  /** A small figure under the value in muted text ("S&P 500 +17.3%"). */
  note?: ReactNode;
  /** A sparkline or other small visual, top-right. Hidden below md. */
  aside?: ReactNode;
  /** A signed result (P/L, return): adds a 3px rule on the left in gain/loss. */
  signed?: "gain" | "loss";
  /** The page's one hero figure: larger. */
  hero?: boolean;
  className?: string;
};

export function KpiChangeLine({ change, className }: { change: KpiChange; className?: string }) {
  const arrow = change.direction === "up" ? "▲" : change.direction === "down" ? "▼" : "";
  return (
    <p
      className={cn(
        "num text-xs",
        change.direction === "up" ? "text-gain" : change.direction === "down" ? "text-loss" : "text-text-muted",
        className,
      )}
    >
      {arrow ? <span aria-hidden="true">{arrow} </span> : null}
      {change.text}
      {change.period ? <span className="text-text-muted"> {change.period}</span> : null}
    </p>
  );
}

export function KpiTile({ label, value, change, verdict, badge, note, aside, signed, hero, className }: KpiTileProps) {
  return (
    <div className={cn("relative min-w-0 px-4 py-4 md:px-6 md:py-5", className)}>
      {signed ? (
        <span
          aria-hidden="true"
          className={cn("absolute top-5 bottom-5 left-0 w-[3px] rounded-r-sm", signed === "gain" ? "bg-gain" : "bg-loss")}
        />
      ) : null}
      {aside ? <div className="absolute top-5 right-5 hidden w-24 md:block">{aside}</div> : null}
      <div className="flex min-w-0 items-center gap-1.5 text-xs text-text-muted">
        <span className="truncate">{label}</span>
        {badge}
      </div>
      <div
        className={cn(
          "num mt-1.5 font-heading font-semibold whitespace-nowrap",
          hero ? "text-[28px] leading-8 md:text-[36px] md:leading-10" : "kpi-figure",
        )}
      >
        {value}
      </div>
      {change ? <KpiChangeLine change={change} className="mt-1" /> : null}
      {note ? <p className="num mt-1 text-xs text-text-muted">{note}</p> : null}
      {verdict ? (
        <VerdictLine verdict={verdict.tone} className="mt-2.5 text-[13px] text-text-muted">
          {verdict.text}
        </VerdictLine>
      ) : null}
    </div>
  );
}

/** A row of KpiTiles in one card: 3-5 across from md, 2×2 below. */
export function KpiStrip({ tiles, label, className }: { tiles: readonly KpiTileProps[]; label?: string; className?: string }) {
  const cols = tiles.length >= 5 ? "md:grid-cols-5" : tiles.length === 3 ? "md:grid-cols-3" : "md:grid-cols-4";
  return (
    <section
      aria-label={label}
      className={cn(
        "blueprint grid grid-cols-2 overflow-hidden",
        cols,
        // Hairlines between tiles: vertical on desktop, a grid of rules below md.
        "[&>*]:border-border-soft max-md:[&>*:nth-child(even)]:border-l max-md:[&>*:nth-child(n+3)]:border-t md:[&>*+*]:border-l",
        // An odd count leaves the last tile alone below md: let it take the whole row.
        tiles.length % 2 === 1 && "max-md:[&>*:last-child]:col-span-2",
        className,
      )}
    >
      {tiles.map((t) => (
        <KpiTile key={t.label} {...t} />
      ))}
    </section>
  );
}
