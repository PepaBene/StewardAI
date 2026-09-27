"use client";

import { CircleHelpIcon, LayersIcon, ShieldAlertIcon, ShieldCheckIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Confidence } from "@/lib/api/client";
import { CONFIDENCE, isLongStandingVerified } from "@/lib/evidence/confidence";
import { useAnchoredPosition } from "@/lib/hooks/use-anchored-position";
import { cn } from "@/lib/utils";

const ICONS = {
  verified: ShieldCheckIcon,
  corroborated: LayersIcon,
  unconfirmed: CircleHelpIcon,
  disputed: ShieldAlertIcon,
} as const satisfies Record<Confidence, unknown>;

/**
 * Exactly one per fact or verdict, placed next to it: label + icon + muted colour.
 *
 * `reason`/`nextAction`, when given, turn the badge into a one-click disclosure ("why this
 * confidence") instead of a bare chip — a small inline panel, never a modal. `since` (the
 * timestamp of the confidence's last change) lets a long-standing Verified badge render
 * quietly: label only, no fill, so it doesn't compete for attention with the states that
 * actually need it.
 */
export function ConfidenceBadge({
  confidence,
  reason,
  nextAction,
  since,
  className,
}: {
  confidence: Confidence;
  reason?: string | null;
  nextAction?: string | null;
  since?: string | Date | null;
  className?: string;
}) {
  const { label, tone, description } = CONFIDENCE[confidence];
  const Icon = ICONS[confidence];
  const quiet = isLongStandingVerified(confidence, since);
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const hasDisclosure = Boolean(reason || nextAction);
  const position = useAnchoredPosition(open && hasDisclosure, anchorRef, panelRef);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (anchorRef.current?.contains(e.target as Node) || panelRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const chipClasses = cn(
    "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
    quiet
      ? "border-transparent bg-transparent text-text-muted"
      : cn(
          "text-text",
          tone === "verified" && "border-verified/40 bg-verified-subtle",
          tone === "corroborated" && "border-corroborated/40 bg-corroborated-subtle",
          tone === "unconfirmed" && "border-unconfirmed/40 bg-unconfirmed-subtle",
          tone === "disputed" && "border-disputed/40 bg-disputed-subtle",
        ),
    className,
  );
  const iconClasses = cn(
    "size-3.5",
    quiet
      ? "text-text-muted"
      : cn(
          tone === "verified" && "text-verified",
          tone === "corroborated" && "text-corroborated",
          tone === "unconfirmed" && "text-unconfirmed",
          tone === "disputed" && "text-disputed",
        ),
  );

  if (!hasDisclosure) {
    return (
      <span title={description} data-confidence={confidence} className={chipClasses}>
        <Icon aria-hidden="true" className={iconClasses} />
        {label}
      </span>
    );
  }

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        data-confidence={confidence}
        aria-expanded={open}
        aria-describedby={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className={cn(chipClasses, "cursor-pointer hover:border-accent focus-visible:border-accent")}
      >
        <Icon aria-hidden="true" className={iconClasses} />
        {label}
      </button>
      {open
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role="tooltip"
              style={position ? { top: position.top, left: position.left } : { top: 0, left: 0, visibility: "hidden" }}
              className="fixed z-50 w-80 max-w-[calc(100vw-1rem)] space-y-1.5 rounded-lg border border-border bg-surface-raised p-3 text-left text-sm font-normal text-text shadow-lg"
            >
              <p className="text-xs font-medium text-text-muted">
                {label}: {description}
              </p>
              {reason ? <p>{reason}</p> : null}
              {nextAction ? <p className="text-text-muted">{nextAction}</p> : null}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
