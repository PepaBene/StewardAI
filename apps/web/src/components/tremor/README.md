# Tremor Raw components

This folder holds [Tremor Raw](https://tremor.so) components (charts, KPI cards,
trackers, data bars). Tremor Raw is copy-paste: the component source lives in the
repo, not behind an npm package.

Copied in so far:

- `spark-chart.tsx` (`SparkAreaChart`/`SparkLineChart`/`SparkBarChart`) — a stat
  tile's 12-point sparkline (Ledger phase 7, visualization E).
- `chart-colors.ts` — adapted per step 2 below: StewardAI token names
  (`neutral`, `accent`, `gain`, `loss`, `categorical-1`..`8`) in place of
  upstream's Tailwind palette keys.
- `get-y-axis-domain.ts` — copied verbatim (no colour content).

Already in place, so copied components work without extra setup:

- Dependencies: `recharts`, `tailwind-variants`, `clsx`, `tailwind-merge`, and
  Radix primitives as needed per component.
- Helpers in `src/lib/utils.ts`: `cx` (alias of `cn`), `focusRing`,
  `focusInput`, `hasErrorInput`, re-expressed with StewardAI colour tokens.
- Tailwind v4 via `@tailwindcss/postcss`, plus a `dark:` custom variant bound to
  the `.dark` class that next-themes sets.

When copying a component in:

1. Replace every Tailwind palette class (the gray/blue/... shade classes
   Tremor ships with, including their `dark:` variants) with a token class
   (`bg-surface-muted`, `text-accent`, ...); token classes already switch with the
   theme, so the `dark:` duplicates can simply be dropped. The default palette is disabled in `src/app/tokens.css`, so palette
   classes render nothing, and `npm run lint:colors` fails on them.
2. Chart series colours must come from tokens too (e.g. `var(--color-gain)`); add
   any new chart tokens to `src/app/tokens.css` for BOTH themes.
3. Add `num` (tabular numerals) to every element that renders a figure.
