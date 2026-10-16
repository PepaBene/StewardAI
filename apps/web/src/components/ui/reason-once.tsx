import { InfoIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Reasons with duplicates (case- and space-insensitive) removed, first occurrence kept. */
export function uniqueReasons(reasons: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of reasons) {
    const t = r?.trim();
    if (!t) continue;
    const k = t.toLowerCase().replace(/\s+/g, " ");
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(t);
  }
  return out;
}

/**
 * ReasonOnce (plan §5.6): a page's caveats said once at the top, not repeated under every
 * panel. One reason reads as a sentence; several collapse behind a count.
 */
export function ReasonOnce({
  reasons,
  title = "What these figures leave out",
  action,
  className,
}: {
  reasons: readonly (string | null | undefined)[];
  title?: string;
  action?: ReactNode;
  className?: string;
}) {
  const list = uniqueReasons(reasons);
  if (list.length === 0) return null;
  return (
    <aside role="note" className={cn("flex items-start gap-2.5 rounded-card bg-surface-muted px-4 py-3 text-[13px] text-text-muted", className)}>
      <InfoIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {list.length === 1 ? (
          <p>{list[0]}</p>
        ) : (
          <details>
            <summary className="cursor-pointer text-text">
              {title}: {list.length} notes
            </summary>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              {list.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </aside>
  );
}
