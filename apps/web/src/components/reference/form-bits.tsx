"use client";

import { CheckIcon, CircleAlertIcon, LoaderCircleIcon } from "lucide-react";
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const CONTROL =
  "w-full min-w-0 border border-border bg-surface-raised px-3 text-base text-text outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal aria-invalid:ring-2 aria-invalid:ring-warning-signal/30 md:text-sm";
export const SELECT_CLASS = cn(CONTROL, "h-9");
export const TEXTAREA_CLASS = cn(CONTROL, "min-h-20 py-2");

/**
 * A labelled form control. The label, hint and error are wired to the control through
 * aria attributes; the error is text, so it never relies on colour alone.
 */
export function Field({
  label,
  hint,
  error,
  className,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
  children: ReactElement<{ id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }>;
}) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-err` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-1", className)}>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {isValidElement(children) ? cloneElement(children, { id, "aria-invalid": error ? true : undefined, "aria-describedby": describedBy }) : children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-err`} className="text-xs font-medium text-text">
          <CircleAlertIcon aria-hidden="true" className="mr-1 inline size-3.5 align-[-2px]" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

export type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "ok"; text: string } | { kind: "error"; text: string };

/** Saving, success and error in one live region, each with an icon and words. */
export function SaveNote({ state, idle }: { state: SaveState; idle?: ReactNode }) {
  return (
    <p role="status" aria-live="polite" className="text-xs text-text-muted">
      {state.kind === "saving" ? (
        <>
          <LoaderCircleIcon aria-hidden="true" className="mr-1 inline size-3.5 animate-spin align-[-2px]" />
          Saving…
        </>
      ) : state.kind === "ok" ? (
        <span className="text-text">
          <CheckIcon aria-hidden="true" className="mr-1 inline size-3.5 align-[-2px]" />
          {state.text}
        </span>
      ) : state.kind === "error" ? (
        <span className="font-medium text-text">
          <CircleAlertIcon aria-hidden="true" className="mr-1 inline size-3.5 align-[-2px]" />
          {state.text}
        </span>
      ) : (
        (idle ?? null)
      )}
    </p>
  );
}

/** Marks a value as typed in by the owner, as opposed to reported by a provider. Dashed outline plus words. */
export function OwnerTag({ children = "Entered by you" }: { children?: ReactNode }) {
  return <span className="inline-block border border-dashed border-border px-1.5 py-0.5 text-xs font-medium text-text-muted">{children}</span>;
}

/** A form frame: a plain bordered block that sits inside a panel and says what it records. */
export function FormFrame({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <fieldset className={cn("space-y-3 border border-border bg-surface-muted p-3", className)}>
      <legend className="px-1 text-sm font-semibold">{title}</legend>
      {children}
    </fieldset>
  );
}
