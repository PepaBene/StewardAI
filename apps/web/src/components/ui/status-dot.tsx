import { cn } from "@/lib/utils";

/**
 * A timeline/list marker coloured by state: shape never encodes state (shape is reserved
 * for filtering), only this dot's colour plus the row's own label and icon do. Shared by
 * every "neutral / info / warning" state taxonomy (severity, job status, ...) so a dot
 * always means the same tone everywhere it appears. Decorative; the row's text remains
 * the accessible source of truth.
 */
export function StatusDot({ tone, className }: { tone: "neutral" | "info" | "warning"; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "size-2.5 shrink-0 rounded-full",
        tone === "neutral" && "bg-neutral-signal",
        tone === "info" && "bg-info-signal",
        tone === "warning" && "bg-warning-signal",
        className,
      )}
    />
  );
}
