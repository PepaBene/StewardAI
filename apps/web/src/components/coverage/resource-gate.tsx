import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/** The data on screen for a resource: current data, else the last good data after a failed refetch. */
export function lastGood<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

/** Loading / error / last-good handling for one resource; a failed refetch keeps the old data beside the error. */
export function ResourceGate<T>({ state, label, children }: { state: ResourceState<T>; label: string; children: (data: T) => ReactNode }) {
  if (state.kind === "loading") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {label}…
      </p>
    );
  }
  if (state.kind === "ok") return <>{children(state.data)}</>;
  if (state.previous !== null) {
    return (
      <div className="flex flex-col gap-3">
        <AlertMessage title={`${label}: refresh failed, showing the last loaded data`}>
          {state.title}. {state.detail}
        </AlertMessage>
        {children(state.previous)}
      </div>
    );
  }
  return (
    <AlertMessage title={`Could not load ${label}`}>
      {state.title}. {state.detail}
    </AlertMessage>
  );
}
