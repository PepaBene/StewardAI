// Tremor Raw's chartColors.ts, adapted: StewardAI's own token names in place of the
// upstream Tailwind palette keys (blue/emerald/violet/...), per this folder's README step 2.
export type ColorUtility = "bg" | "stroke" | "fill" | "text";

export const chartColors = {
  neutral: {
    bg: "bg-neutral-signal",
    stroke: "stroke-neutral-signal",
    fill: "fill-neutral-signal",
    text: "text-neutral-signal",
  },
  accent: { bg: "bg-accent", stroke: "stroke-accent", fill: "fill-accent", text: "text-accent-ink" },
  gain: { bg: "bg-gain", stroke: "stroke-gain", fill: "fill-gain", text: "text-gain" },
  loss: { bg: "bg-loss", stroke: "stroke-loss", fill: "fill-loss", text: "text-loss" },
  "categorical-1": {
    bg: "bg-categorical-1",
    stroke: "stroke-categorical-1",
    fill: "fill-categorical-1",
    text: "text-categorical-1",
  },
  "categorical-2": {
    bg: "bg-categorical-2",
    stroke: "stroke-categorical-2",
    fill: "fill-categorical-2",
    text: "text-categorical-2",
  },
  "categorical-3": {
    bg: "bg-categorical-3",
    stroke: "stroke-categorical-3",
    fill: "fill-categorical-3",
    text: "text-categorical-3",
  },
  "categorical-4": {
    bg: "bg-categorical-4",
    stroke: "stroke-categorical-4",
    fill: "fill-categorical-4",
    text: "text-categorical-4",
  },
  "categorical-5": {
    bg: "bg-categorical-5",
    stroke: "stroke-categorical-5",
    fill: "fill-categorical-5",
    text: "text-categorical-5",
  },
  "categorical-6": {
    bg: "bg-categorical-6",
    stroke: "stroke-categorical-6",
    fill: "fill-categorical-6",
    text: "text-categorical-6",
  },
  "categorical-7": {
    bg: "bg-categorical-7",
    stroke: "stroke-categorical-7",
    fill: "fill-categorical-7",
    text: "text-categorical-7",
  },
  "categorical-8": {
    bg: "bg-categorical-8",
    stroke: "stroke-categorical-8",
    fill: "fill-categorical-8",
    text: "text-categorical-8",
  },
} as const satisfies { [color: string]: { [key in ColorUtility]: string } };

export type AvailableChartColorsKeys = keyof typeof chartColors;

export const AvailableChartColors: AvailableChartColorsKeys[] = Object.keys(
  chartColors,
) as AvailableChartColorsKeys[];

export const constructCategoryColors = (
  categories: string[],
  colors: AvailableChartColorsKeys[],
): Map<string, AvailableChartColorsKeys> => {
  const categoryColors = new Map<string, AvailableChartColorsKeys>();
  categories.forEach((category, index) => {
    categoryColors.set(category, colors[index % colors.length]);
  });
  return categoryColors;
};

export const getColorClassName = (color: AvailableChartColorsKeys, type: ColorUtility): string => {
  const fallback = chartColors.neutral;
  return chartColors[color]?.[type] ?? fallback[type];
};
