import { cn } from "@/lib/utils";

/**
 * A square on/off switch (role="switch"). The knob's side and aria-checked carry the state,
 * never the fill colour alone.
 */
export function ToggleSwitch({
  checked,
  onChange,
  label,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Accessible name, e.g. "Rule nvda-cap enabled". */
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={`${label}: ${checked ? "on" : "off"}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-[18px] w-[34px] shrink-0 border transition-colors disabled:opacity-50",
        checked ? "border-accent bg-accent" : "border-border bg-surface-muted",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute top-px size-3.5 transition-[left]",
          checked ? "left-[17px] bg-accent-foreground" : "left-px bg-text-faint",
        )}
      />
    </button>
  );
}
