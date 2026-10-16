import { CircleCheckIcon, CircleDashedIcon, CircleXIcon, TriangleAlertIcon } from "lucide-react";

import { statusWord, type GraphStatus } from "@/lib/relationships/relationships";
import { cn } from "@/lib/utils";

/*
 * Status is told by shape, border style and word together, never by colour: confirmed is a
 * solid-bordered check, suggested a dashed circle, disputed a double-bordered triangle,
 * rejected a dotted cross. A null status is "awaiting a decision", drawn like suggested.
 */
const MARK: Record<GraphStatus, { Icon: typeof CircleCheckIcon; border: string; text: string }> = {
  confirmed: { Icon: CircleCheckIcon, border: "border-solid border-text", text: "font-semibold" },
  suggested: { Icon: CircleDashedIcon, border: "border-dashed border-border", text: "" },
  disputed: { Icon: TriangleAlertIcon, border: "border-double border-4 border-text", text: "font-semibold" },
  rejected: { Icon: CircleXIcon, border: "border-dotted border-border", text: "text-text-muted line-through decoration-1" },
};

export function StatusMark({ status, className }: { status: GraphStatus | null; className?: string }) {
  const m = MARK[status ?? "suggested"];
  const word = status ? statusWord(status) : "Awaiting decision";
  return (
    <span className={cn("inline-flex items-center gap-1 border bg-surface-raised px-1.5 py-0.5 text-xs leading-none", m.border, m.text, className)}>
      <m.Icon aria-hidden="true" className="size-3" />
      {word}
    </span>
  );
}

/** A labelled value in a definition list; absent values read as "not recorded", never blank or zero. */
export function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="text-sm break-words">{children ?? <span className="text-text-muted">not recorded</span>}</dd>
    </div>
  );
}

export function SourceLink({ href, label = "Open source" }: { href: string | null; label?: string }) {
  if (!href) return <span className="text-text-muted">No source address</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent-ink hover:underline">
      {label}
    </a>
  );
}
