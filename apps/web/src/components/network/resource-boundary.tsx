"use client";

import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { StatePlaceholder } from "@/components/data-state";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import type { Parsed } from "@/lib/network/types";

/**
 * Renders a parsed API resource: loading, error (the last good value stays beside the
 * message), a rejected response shape (shown as unavailable, never as an empty list) or the
 * value. `title` frames the loading and failure states so each panel degrades on its own.
 */
export function ResourceBoundary<T>({
  state,
  title,
  kicker,
  children,
}: {
  state: ResourceState<Parsed<T>>;
  title: string;
  kicker?: string;
  children: (value: T, stale: boolean) => ReactNode;
}) {
  const frame = (body: ReactNode) => (
    <Blueprint as="section" aria-label={title} className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker={kicker} title={title} />
      {body}
    </Blueprint>
  );
  if (state.kind === "loading") {
    return frame(
      <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading…
      </p>,
    );
  }
  if (state.kind === "error") {
    const previous = state.previous;
    if (previous && previous.ok) {
      return (
        <div className="flex flex-col gap-3">
          <AlertMessage title={`${title}: refresh failed, showing the last loaded data`}>
            {state.title}. {state.detail}
          </AlertMessage>
          {children(previous.value, true)}
        </div>
      );
    }
    return frame(
      <>
        <AlertMessage title={state.title}>{state.detail}</AlertMessage>
        <StatePlaceholder state="unavailable" reason="The request failed, so no figure is shown. Nothing here is a zero." minHeight={80} />
      </>,
    );
  }
  if (!state.data.ok) {
    return frame(
      <StatePlaceholder
        state="unavailable"
        reason={`The API answered with a shape this screen does not recognise (${state.data.reason}). It is shown as unavailable rather than as an empty result.`}
        minHeight={80}
      />,
    );
  }
  return <>{children(state.data.value, false)}</>;
}
