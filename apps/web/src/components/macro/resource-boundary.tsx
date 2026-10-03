"use client";

import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { StatePlaceholder } from "@/components/data-state";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/**
 * The shared loading / error / not-found shell of a section that reads one API resource. An
 * error keeps the previous data on screen next to the alert (useApiResource's last-good rule);
 * a 404 is the API saying it has nothing for this request, which is "not covered", not a failure.
 */
export function ResourceBoundary<T>({
  state,
  what,
  notFound,
  children,
}: {
  state: ResourceState<T>;
  /** What is loading, for the status line and error title ("portfolio country exposure"). */
  what: string;
  /** Replaces the default placeholder for HTTP 404. */
  notFound?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (state.kind === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {what}…
      </p>
    );
  }
  if (state.kind === "ok") return <>{children(state.data)}</>;
  if (state.status === 404 && state.previous === null) {
    return notFound ? <>{notFound}</> : <StatePlaceholder state="not_covered" reason={state.detail || `The API has no ${what} for this request.`} />;
  }
  return (
    <div className="flex flex-col gap-3">
      <AlertMessage title={`Could not load ${what}`}>
        {state.title}
        {state.detail ? `: ${state.detail}` : ""}
        {state.previous !== null ? " The last data received is still shown." : ""}
      </AlertMessage>
      {state.previous !== null ? children(state.previous) : <StatePlaceholder state="unavailable" reason={`${state.title}${state.detail ? `: ${state.detail}` : ""}`} />}
    </div>
  );
}
