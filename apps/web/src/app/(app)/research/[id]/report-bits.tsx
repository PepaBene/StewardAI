import type { ReactNode } from "react";

import { Blueprint } from "@/components/ui/blueprint";
import { cn } from "@/lib/utils";

/** A framed report panel with a kicker heading and an optional one-line hint. */
export function Panel({
  id,
  title,
  hint,
  children,
  className,
}: {
  id: string;
  title: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Blueprint className={cn("flex min-w-0 flex-col gap-3 px-5 py-[18px]", className)} aria-labelledby={id}>
      <h2 id={id} className="kicker font-sans">
        {title}
      </h2>
      {hint ? <p className="text-xs text-text-muted">{hint}</p> : null}
      {children}
    </Blueprint>
  );
}

/** A small bordered label; meaning is always in its words, never in colour alone. */
export function Tag({ children, title, className }: { children: ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 border border-border px-1 text-xs whitespace-nowrap text-text-muted", className)}>
      {children}
    </span>
  );
}

/** A bulleted list of short notes; renders nothing when empty. */
export function NoteList({ lines, className }: { lines: readonly string[]; className?: string }) {
  if (lines.length === 0) return null;
  return (
    <ul className={cn("list-disc space-y-0.5 pl-5 text-sm", className)}>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}
