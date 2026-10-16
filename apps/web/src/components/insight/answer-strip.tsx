import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * The answer strip every v2 screen opens with: the one figure that answers the page's question,
 * large, with its sentence, then three or four supporting figures. Text stays in text tokens.
 */

export type StripFigure = { label: string; value: ReactNode; note?: ReactNode };

export function AnswerStrip({
  lead,
  figures,
  chip,
  className,
}: {
  lead: { label: string; value: ReactNode; sentence?: ReactNode };
  figures: readonly StripFigure[];
  /** Coverage or status chip under the lead. */
  chip?: ReactNode;
  className?: string;
}) {
  return (
    <section aria-label={lead.label} className={cn("grid gap-x-8 gap-y-4 border-y border-border py-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,2fr)]", className)}>
      <div className="min-w-0">
        <p className="text-sm text-text-muted">{lead.label}</p>
        <p className="num mt-1 font-heading text-[40px] leading-none font-semibold tracking-tight sm:text-[52px]">{lead.value}</p>
        {lead.sentence ? <p className="mt-2 max-w-prose text-sm text-text-muted">{lead.sentence}</p> : null}
        {chip ? <div className="mt-2">{chip}</div> : null}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 lg:self-end">
        {figures.map((f) => (
          <div key={f.label} className="min-w-0 border-l border-border-soft pl-3">
            <dt className="text-xs text-text-muted">{f.label}</dt>
            <dd className="num mt-0.5 text-lg font-semibold">{f.value}</dd>
            {f.note ? <dd className="text-[11px] text-text-faint">{f.note}</dd> : null}
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Coverage chip: "12 of 14 paying holdings have a schedule". */
export function CoverageChip({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "partial" | "unavailable" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs",
        tone === "neutral" && "border-border text-text-muted",
        tone === "partial" && "border-warning-signal text-text",
        tone === "unavailable" && "border-loss text-text",
      )}
    >
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", tone === "neutral" ? "bg-gain" : tone === "partial" ? "bg-warning-signal" : "bg-loss")} />
      {children}
    </span>
  );
}
