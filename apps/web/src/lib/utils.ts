import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui class helper: merge conditional classes, later Tailwind classes win. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Tremor Raw names the same helper `cx`; kept as an alias so copy-pasted code works. */
export const cx = cn;

/*
 * Tremor Raw shared class helpers, re-expressed with StewardAI tokens instead
 * of Tailwind palette colours (the upstream versions use blue/gray shades).
 */
export const focusRing = [
  "outline outline-offset-2 outline-0 focus-visible:outline-2",
  "outline-focus",
];

export const focusInput = [
  "focus:ring-2",
  "focus:ring-focus/30",
  "focus:border-focus",
];

export const hasErrorInput = [
  "ring-2",
  "border-warning-signal",
  "ring-warning-signal/30",
];
