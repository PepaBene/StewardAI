import type { ReactNode } from "react";

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface-muted px-6 py-12 text-center">
      <p className="text-base font-medium text-text">{title}</p>
      {children ? (
        <div className="mx-auto mt-2 max-w-prose text-sm text-text-muted">{children}</div>
      ) : null}
    </div>
  );
}
