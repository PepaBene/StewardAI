import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * PromptCard (visual spec B44): one inline nudge with an accent edge: a question, one
 * sentence on why it matters, a primary action and "Not now". At most one per page.
 */
export function PromptCard({
  title,
  children,
  action,
  onDismiss,
  className,
}: {
  title: ReactNode;
  children?: ReactNode;
  action: ReactNode;
  onDismiss?: () => void;
  className?: string;
}) {
  return (
    <aside className={cn("blueprint relative flex flex-wrap items-center gap-x-6 gap-y-3 py-4 pr-5 pl-6", className)}>
      <span aria-hidden="true" className="absolute top-4 bottom-4 left-0 w-[3px] rounded-r-sm bg-accent" />
      <div className="min-w-0 flex-1">
        <h3 className="text-[15px] text-text">{title}</h3>
        {children ? <p className="mt-0.5 text-sm text-text-muted">{children}</p> : null}
      </div>
      <div className="flex items-center gap-2">
        {action}
        {onDismiss ? (
          <button type="button" onClick={onDismiss} className="h-9 rounded-control px-3 text-[13px] text-text-muted hover:bg-surface-muted hover:text-text">
            Not now
          </button>
        ) : null}
      </div>
    </aside>
  );
}
