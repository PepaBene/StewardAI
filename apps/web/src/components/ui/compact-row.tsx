import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * CompactRow (spec B42): the mobile form of a table row, 64-72 px. Line 1: avatar, name and the
 * primary value; line 2: a symbol chip and up to two secondary values. A chevron when it links.
 */
export function CompactRow({
  avatar,
  name,
  symbol,
  primary,
  secondary,
  href,
  onClick,
  className,
}: {
  avatar?: ReactNode;
  name: ReactNode;
  symbol?: ReactNode;
  primary?: ReactNode;
  /** Up to two short values for line 2, right-aligned. */
  secondary?: readonly ReactNode[];
  href?: string;
  onClick?: () => void;
  className?: string;
}) {
  const body = (
    <>
      {avatar ? <span className="shrink-0">{avatar}</span> : null}
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-3">
          <span className="truncate text-[15px] font-medium text-text">{name}</span>
          {primary != null ? <span className="num shrink-0 text-[15px] text-text">{primary}</span> : null}
        </span>
        <span className="mt-0.5 flex items-center justify-between gap-3 text-xs text-text-muted">
          {symbol != null ? <span className="num rounded-full bg-surface-muted px-2 py-px">{symbol}</span> : <span />}
          {secondary && secondary.length > 0 ? (
            <span className="num flex shrink-0 gap-3">
              {secondary.slice(0, 2).map((s, i) => (
                <span key={i}>{s}</span>
              ))}
            </span>
          ) : null}
        </span>
      </span>
      {href || onClick ? <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-text-faint" /> : null}
    </>
  );
  const base = cn("flex min-h-16 items-center gap-3 px-4 py-3", (href || onClick) && "hover:bg-surface-muted", className);
  if (href)
    return (
      <Link href={href} className={base}>
        {body}
      </Link>
    );
  if (onClick)
    return (
      <button type="button" onClick={onClick} className={cn(base, "w-full text-left")}>
        {body}
      </button>
    );
  return <div className={base}>{body}</div>;
}
