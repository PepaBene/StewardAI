"use client";

import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { StatePlaceholder } from "@/components/data-state";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/**
 * Renders an API resource: loading text, a placeholder when there is no value to show, and
 * on a failed refetch the last good value with the error beside it (never a silent swap).
 * `children` receives the data and whether it is a last-good value after a failed refresh.
 */
export function ResourceShell<T>({
  state,
  label,
  children,
}: {
  state: ResourceState<T>;
  label: string;
  children: (data: T, lastGood: boolean) => ReactNode;
}) {
  if (state.kind === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {label}…
      </p>
    );
  }
  if (state.kind === "error") {
    if (state.previous === null) {
      return (
        <StatePlaceholder
          state="unavailable"
          reason={`${label}: ${state.title}${state.detail ? ` — ${state.detail}` : ""}`}
        />
      );
    }
    return (
      <div className="flex flex-col gap-3">
        <AlertMessage title={`${label}: ${state.title}`}>
          {state.detail ? `${state.detail} ` : null}Showing the last value that loaded.
        </AlertMessage>
        {children(state.previous, true)}
      </div>
    );
  }
  return <>{children(state.data, false)}</>;
}

/** A framed section with a heading and children; used where ChartFrame (chart + provenance) is too much. */
export function Panel({
  id,
  kicker,
  title,
  description,
  action,
  children,
}: {
  id: string;
  kicker?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Blueprint as="section" aria-labelledby={`${id}-heading`} id={id} className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker={kicker} title={<span id={`${id}-heading`}>{title}</span>} action={action} />
      {description ? <p className="max-w-prose text-sm text-text-muted">{description}</p> : null}
      {children}
    </Blueprint>
  );
}
