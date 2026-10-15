"use client";

import { Button } from "@/components/ui/button";

export type OverrideReason =
  | "wrong_data"
  | "wrong_source"
  | "right_but_unimportant"
  | "too_early"
  | "too_late"
  | "prefer_my_judgement"
  | "other";

const REASONS: { code: OverrideReason; label: string }[] = [
  { code: "wrong_data", label: "The data was wrong" },
  { code: "wrong_source", label: "Wrong source" },
  { code: "right_but_unimportant", label: "Right, but not worth acting on" },
  { code: "too_early", label: "Too early" },
  { code: "too_late", label: "Too late" },
  { code: "prefer_my_judgement", label: "I prefer to decide this" },
];

/** One tap to say why you overruled the autopilot. Skipping is always allowed. */
export function ReasonChips({
  disabled,
  onPick,
  onCancel,
}: {
  disabled?: boolean;
  onPick: (reason: OverrideReason | null) => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="group"
      aria-label="Why are you overruling this?"
      className="mt-3 flex flex-wrap items-center gap-2"
    >
      <span className="text-xs text-text-muted">Why?</span>
      {REASONS.map((r) => (
        <Button
          key={r.code}
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={() => onPick(r.code)}
        >
          {r.label}
        </Button>
      ))}
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled}
        onClick={() => onPick(null)}
      >
        Skip
      </Button>
      <Button size="sm" variant="ghost" disabled={disabled} onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
