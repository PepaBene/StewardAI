import { CircleDashedIcon } from "lucide-react";
import type { ReactNode } from "react";

/** Nothing to show yet: a dashed frame, a condensed title and what to do about it. */
export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="border border-dashed border-border px-6 py-10 text-center">
      <CircleDashedIcon aria-hidden="true" className="mx-auto mb-2 size-5 text-text-faint" />
      <p className="font-heading text-[20px] leading-tight font-semibold text-text">{title}</p>
      {children ? (
        <div className="mx-auto mt-1.5 max-w-prose text-sm text-text-muted">{children}</div>
      ) : null}
    </div>
  );
}
