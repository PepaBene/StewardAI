import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { StatePlaceholder } from "@/components/data-state";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import type { Parsed } from "@/lib/markets/types";

/**
 * Loading, error and last-good handling for one resource. With data (or the previous data when
 * a refetch failed) it renders `children`, the failure shown beside it; without any it renders
 * the error. `notFound` replaces the error for an HTTP 404, which for some endpoints is an
 * answer (no listing derived yet), not a failure.
 */
export function ResourceGate<T>({
  state,
  label,
  notFound,
  children,
}: {
  state: ResourceState<T>;
  label: string;
  notFound?: ReactNode;
  children: (data: T) => ReactNode;
}) {
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
  if (state.status === 404 && notFound) return <>{notFound}</>;
  return (
    <AlertMessage title={`Could not load ${label}`}>
      {state.title}. {state.detail}
    </AlertMessage>
  );
}

/** A parsed untyped body: the data, or the reason the shape was rejected as an unavailable state. */
export function ParsedGate<T>({ parsed, label, children }: { parsed: Parsed<T>; label: string; children: (data: T) => ReactNode }) {
  if (parsed.ok) return <>{children(parsed.data)}</>;
  return <StatePlaceholder state="unavailable" reason={`The ${label} response did not match the expected shape (${parsed.reason}). Nothing is shown rather than a guess.`} />;
}

/** The data on screen for a resource: the current data, else the last good data after a failed refetch. */
export function lastGood<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}
