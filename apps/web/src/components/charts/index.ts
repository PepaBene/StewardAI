/*
 * StewardAI chart kit: small, typed, dependency-free SVG/HTML charts in the Industry style
 * (thin strokes, square marks, mono tabular axis labels, blue-dominant). Every colour comes
 * from a token class, so light and dark themes both work; every chart renders a quiet empty
 * state (or nothing, for word-sized sparklines) instead of inventing data. None use hooks,
 * so they render in server and client components alike.
 *
 * Pure geometry and maths live in src/lib/charts (unit-tested).
 */

export { Sparkline, SparkArea, type SparklineProps } from "./sparkline";
export { LineChart, type LineChartProps, type LinePoint } from "./line-chart";
export { CandleChart, type CandleChartProps, type OhlcBar } from "./candle-chart";
export { DrawdownChart, type DrawdownChartProps } from "./drawdown-chart";
export { Donut, type DonutProps, type DonutSegment } from "./donut";
export { Ring, ProgressRing, Gauge, type RingProps, type GaugeProps } from "./ring";
export { Radar, type RadarProps, type RadarSeries } from "./radar";
export {
  Heatmap,
  HeatScale,
  HEAT_BG,
  HEAT_TEXT,
  rowsFromMonthlyReturns,
  type HeatmapProps,
  type HeatmapRow,
} from "./heat";
export { Treemap, type TreemapItem, type TreemapProps } from "./treemap";
export { VintageFan, type VintageFanPoint, type VintageFanProps } from "./vintage-fan";
export { WaterfallChart, type WaterfallChartProps, type WaterfallStep } from "./waterfall";
export { LorenzChart, type LorenzChartProps } from "./lorenz";
export { Dumbbell, type DumbbellProps, type DumbbellRow } from "./dumbbell";
export { AuditTrail, type AuditTrailProps, type AuditTrailPoint } from "./audit-trail";
export { RevisionLine, type RevisionLineProps, type RevisionLinePoint } from "./revision-line";
export { Sankey,type SankeyProps, type SankeyLinkInput, type SankeyNodeInput } from "./sankey";
export { BarChart, Histogram, type BarChartProps, type BarDatum, type HistogramProps } from "./bar-chart";
export {
  HBar,
  Meter,
  DivergingBar,
  StackedBar,
  type HBarProps,
  type DivergingBarProps,
  type DivergingBarRow,
  type StackedBarProps,
  type StackedSegment,
} from "./hbar";
export {
  GridStatus,
  StatusMatrix,
  GRID_STATUS_LEGEND,
  type GridCellStatus,
  type GridStatusProps,
  type GridStatusRow,
} from "./grid-status";
export {
  ChartEmpty,
  Legend,
  LegendKey,
  SrTable,
  CATEGORICAL_BG,
  CATEGORICAL_FILL,
  TONE_BG,
  TONE_FILL,
  TONE_STROKE,
  type ChartTone,
  type LegendEntry,
  type LegendSwatch,
} from "./shared";
export { ActivityCalendar, type ActivityCalendarProps } from "./activity-calendar";
