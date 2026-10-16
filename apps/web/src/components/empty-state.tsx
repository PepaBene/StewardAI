import { CircleDashedIcon } from "lucide-react";
import type { ReactNode } from "react";

/** An empty screen as an invitation to act: what is missing, then the one step that fills it. */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  /** The next step, e.g. a button that adds the first item. */
  action?: ReactNode;
}) {
  return (
    <div className="rounded-card bg-surface-muted px-6 py-10 text-center">
      <CircleDashedIcon aria-hidden="true" className="mx-auto mb-2 size-5 text-text-faint" />
      <p className="font-heading text-[20px] leading-tight font-semibold text-text">{title}</p>
      {children ? <div className="mx-auto mt-1.5 max-w-prose text-sm text-text-muted">{children}</div> : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  );
}
