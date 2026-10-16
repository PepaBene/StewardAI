"use client";

import { useEntityNames } from "@/lib/entities/use-entity-names";
import { shortTicker } from "@/lib/portfolio/ticker";

/**
 * A holding by name, not by Trading212 id: "Apple" with a small "AAPL" chip. The full raw id
 * stays in the `title`. When no name is known (passed or resolved) only the short ticker shows.
 */
export function EntityName({
  id,
  name,
  chip = true,
  className,
}: {
  id: string;
  name?: string | null;
  chip?: boolean;
  className?: string;
}) {
  const names = useEntityNames();
  const resolved = name || names.get(id) || null;
  const symbol = shortTicker(id);
  return (
    <span className={className} title={id}>
      {resolved ?? symbol}
      {resolved && chip && resolved !== symbol ? (
        <span className="ml-1.5 rounded-sm bg-surface-muted px-1.5 text-xs text-text-muted">{symbol}</span>
      ) : null}
    </span>
  );
}
