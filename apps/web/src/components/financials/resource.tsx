import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/**
 * Loading / error / ok for one resource. A failed refetch keeps the last good data on screen
 * under an alert; a first-time failure shows only the alert (never an empty chart).
 */
export function ResourceBoundary<T>({ state, label, children }: { state: ResourceState<T>; label: string; children: (data: T) => ReactNode }) {
  if (state.kind === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {label}…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <AlertMessage title={`${label} unavailable: ${state.title}`}>{state.detail}</AlertMessage>
        {state.previous ? children(state.previous) : null}
      </div>
    );
  }
  return <>{children(state.data)}</>;
}
