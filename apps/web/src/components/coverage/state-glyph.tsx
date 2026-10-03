import {
  BanIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  CircleHelpIcon,
  ClockAlertIcon,
  GaugeIcon,
  PieChartIcon,
  ScaleIcon,
  ServerCrashIcon,
  type LucideIcon,
} from "lucide-react";

import { DATA_STATES, DATA_STATE_META, STATE_TONE_CLASSES, type DataState } from "@/lib/data-state/states";
import { cn } from "@/lib/utils";

export const STATE_ICONS: Record<DataState, LucideIcon> = {
  verified: CircleCheckIcon,
  stale: ClockAlertIcon,
  partial: PieChartIcon,
  not_covered: CircleDashedIcon,
  paid_only: BanIcon,
  quota_exhausted: GaugeIcon,
  access_unverified: CircleHelpIcon,
  disputed: ScaleIcon,
  unavailable: ServerCrashIcon,
};

/** Human description of each fill pattern (the second, non-colour encoding of a state). */
export const STATE_PATTERN_NAME: Record<DataState, string> = {
  verified: "solid",
  stale: "diagonal lines",
  partial: "vertical lines",
  not_covered: "dots",
  paid_only: "cross-hatch",
  quota_exhausted: "horizontal lines",
  access_unverified: "sparse dots",
  disputed: "checks",
  unavailable: "reverse diagonal lines",
};

const PATTERNS: Record<DataState, React.ReactNode> = {
  verified: null,
  stale: <path d="M-1,1 l2,-2 M0,8 l8,-8 M7,9 l2,-2" />,
  partial: <path d="M2,0 V8 M6,0 V8" />,
  not_covered: <circle cx="4" cy="4" r="1" />,
  paid_only: <path d="M0,0 L8,8 M8,0 L0,8" />,
  quota_exhausted: <path d="M0,2 H8 M0,6 H8" />,
  access_unverified: <circle cx="4" cy="4" r="0.6" />,
  disputed: <path d="M0,0 H4 V4 H0 Z M4,4 H8 V8 H4 Z" />,
  unavailable: <path d="M-1,7 l2,2 M0,0 l8,8 M7,-1 l2,2" />,
};

/** Hidden SVG holding the nine fill patterns; render once per page, cells reference `#atlas-pat-<state>`. */
export function PatternDefs() {
  return (
    <svg aria-hidden="true" focusable="false" width="0" height="0" className="absolute">
      <defs>
        {DATA_STATES.map((s) => (
          <pattern key={s} id={`atlas-pat-${s}`} width="8" height="8" patternUnits="userSpaceOnUse">
            <g stroke="currentColor" fill={s === "not_covered" || s === "access_unverified" || s === "disputed" ? "currentColor" : "none"} strokeWidth="1">
              {PATTERNS[s]}
            </g>
          </pattern>
        ))}
      </defs>
    </svg>
  );
}

/** A state's pattern as an absolutely-filling layer (needs `PatternDefs` on the page). */
export function PatternFill({ state }: { state: DataState }) {
  if (state === "verified") return null;
  return (
    <svg aria-hidden="true" focusable="false" className="pointer-events-none absolute inset-0 size-full text-text-faint opacity-60">
      <rect width="100%" height="100%" fill={`url(#atlas-pat-${state})`} />
    </svg>
  );
}

/** Small swatch of a state: tone, pattern and icon together. */
export function StateSwatch({ state, className }: { state: DataState; className?: string }) {
  const Icon = STATE_ICONS[state];
  return (
    <span
      className={cn("relative inline-flex size-6 shrink-0 items-center justify-center overflow-hidden border", STATE_TONE_CLASSES[DATA_STATE_META[state].tone].chip, className)}
    >
      <PatternFill state={state} />
      <Icon aria-hidden="true" className="relative size-3.5" />
    </span>
  );
}

/** Legend: every state with its icon, pattern name and meaning. */
export function GlyphLegend() {
  return (
    <ul className="m-0 grid list-none grid-cols-1 gap-x-6 gap-y-1.5 p-0 text-xs sm:grid-cols-2 xl:grid-cols-3" aria-label="State key">
      {DATA_STATES.map((s) => (
        <li key={s} className="flex items-start gap-2">
          <StateSwatch state={s} />
          <span className="min-w-0">
            <span className="font-medium text-text">{DATA_STATE_META[s].label}</span>
            <span className="text-text-muted"> · {STATE_PATTERN_NAME[s]}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
