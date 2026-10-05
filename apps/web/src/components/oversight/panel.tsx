"use client";

import { LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { BlueprintCorners } from "@/components/ui/blueprint";
import type { ResourceState } from "@/lib/hooks/use-api-resource";

/** The data a resource has now: fresh, or the last good copy while a reload failed. */
export function dataOf<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

/** Loading and error lines for a panel; renders nothing once data is there. */
export function ResourceNotice<T>({ state, what }: { state: ResourceState<T>; what: string }) {
  if (state.kind === "loading")
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {what}…
      </p>
    );
  if (state.kind === "error")
    return (
      <AlertMessage title={`Could not load ${what}: ${state.title}`}>
        {state.detail}
        {state.previous ? " Showing the last data loaded." : null}
      </AlertMessage>
    );
  return null;
}

/** A blueprint-framed page section with a heading and a one-paragraph explanation. */
export function Panel({ id, title, description, children }: { id: string; title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div>
        <h2 id={`${id}-heading`} className="text-[22px] leading-tight font-semibold">
          {title}
        </h2>
        {description ? <p className="max-w-[68ch] text-sm text-text-muted">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** In-page "On this page" jump list, as on Data sources. */
export function SectionNav({ sections }: { sections: readonly { id: string; label: string }[] }) {
  return (
    <nav aria-label="On this page" className="border-y border-border-soft py-2">
      <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {sections.map((s) => (
          <li key={s.id}>
            <a href={`#${s.id}`} className="text-accent-ink hover:underline">
              {s.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
