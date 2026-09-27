// Tremor Spark Chart [v1.0.0], copied from tremorlabs/tremor and adapted per this folder's
// README: Tailwind palette classes replaced with StewardAI token classes (chart-colors.ts),
// `cx` replaced with the existing `cn` helper. Structure otherwise unchanged from upstream.
"use client";

import React from "react";
import {
  Area,
  Bar,
  Line,
  AreaChart as RechartsAreaChart,
  BarChart as RechartsBarChart,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import type { AxisDomain } from "recharts/types/util/types";

import { cn } from "@/lib/utils";

import {
  AvailableChartColors,
  type AvailableChartColorsKeys,
  constructCategoryColors,
  getColorClassName,
} from "./chart-colors";
import { getYAxisDomain } from "./get-y-axis-domain";

//#region SparkAreaChart

interface SparkAreaChartProps extends React.HTMLAttributes<HTMLDivElement> {
  data: Record<string, unknown>[];
  categories: string[];
  index: string;
  colors?: AvailableChartColorsKeys[];
  autoMinValue?: boolean;
  minValue?: number;
  maxValue?: number;
  connectNulls?: boolean;
  type?: "default" | "stacked" | "percent";
  fill?: "gradient" | "solid" | "none";
}

export const SparkAreaChart = React.forwardRef<HTMLDivElement, SparkAreaChartProps>(
  (props, forwardedRef) => {
    const {
      data = [],
      categories = [],
      index,
      colors = AvailableChartColors,
      autoMinValue = false,
      minValue,
      maxValue,
      connectNulls = false,
      type = "default",
      className,
      fill = "gradient",
      ...other
    } = props;

    const categoryColors = constructCategoryColors(categories, colors);
    const yAxisDomain = getYAxisDomain(autoMinValue, minValue, maxValue);
    const stacked = type === "stacked" || type === "percent";
    const areaId = React.useId();

    const getFillContent = (fillType: SparkAreaChartProps["fill"]) => {
      switch (fillType) {
        case "none":
          return <stop stopColor="currentColor" stopOpacity={0} />;
        case "solid":
          return <stop stopColor="currentColor" stopOpacity={0.3} />;
        case "gradient":
        default:
          return (
            <>
              <stop offset="5%" stopColor="currentColor" stopOpacity={0.4} />
              <stop offset="95%" stopColor="currentColor" stopOpacity={0} />
            </>
          );
      }
    };

    return (
      <div ref={forwardedRef} className={cn("h-12 w-28", className)} {...other}>
        <ResponsiveContainer>
          <RechartsAreaChart
            data={data}
            margin={{ bottom: 1, left: 1, right: 1, top: 1 }}
            stackOffset={type === "percent" ? "expand" : undefined}
          >
            <XAxis hide dataKey={index} />
            <YAxis hide domain={yAxisDomain as AxisDomain} />
            {categories.map((category) => {
              const categoryId = `${areaId}-${category.replace(/[^a-zA-Z0-9]/g, "")}`;
              return (
                <React.Fragment key={category}>
                  <defs>
                    <linearGradient
                      className={cn(
                        getColorClassName(categoryColors.get(category) as AvailableChartColorsKeys, "text"),
                      )}
                      id={categoryId}
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      {getFillContent(fill)}
                    </linearGradient>
                  </defs>
                  <Area
                    className={cn(
                      getColorClassName(categoryColors.get(category) as AvailableChartColorsKeys, "stroke"),
                    )}
                    dot={false}
                    strokeOpacity={1}
                    name={category}
                    type="linear"
                    dataKey={category}
                    stroke=""
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    isAnimationActive={false}
                    connectNulls={connectNulls}
                    stackId={stacked ? "stack" : undefined}
                    fill={`url(#${categoryId})`}
                  />
                </React.Fragment>
              );
            })}
          </RechartsAreaChart>
        </ResponsiveContainer>
      </div>
    );
  },
);
SparkAreaChart.displayName = "SparkAreaChart";

//#region SparkLineChart

interface SparkLineChartProps extends React.HTMLAttributes<HTMLDivElement> {
  data: Record<string, unknown>[];
  categories: string[];
  index: string;
  colors?: AvailableChartColorsKeys[];
  autoMinValue?: boolean;
  minValue?: number;
  maxValue?: number;
  connectNulls?: boolean;
}

export const SparkLineChart = React.forwardRef<HTMLDivElement, SparkLineChartProps>(
  (props, forwardedRef) => {
    const {
      data = [],
      categories = [],
      index,
      colors = AvailableChartColors,
      autoMinValue = false,
      minValue,
      maxValue,
      connectNulls = false,
      className,
      ...other
    } = props;

    const categoryColors = constructCategoryColors(categories, colors);
    const yAxisDomain = getYAxisDomain(autoMinValue, minValue, maxValue);

    return (
      <div ref={forwardedRef} className={cn("h-12 w-28", className)} {...other}>
        <ResponsiveContainer>
          <RechartsLineChart data={data} margin={{ bottom: 1, left: 1, right: 1, top: 1 }}>
            <XAxis hide dataKey={index} />
            <YAxis hide domain={yAxisDomain as AxisDomain} />
            {categories.map((category) => (
              <Line
                className={cn(getColorClassName(categoryColors.get(category) as AvailableChartColorsKeys, "stroke"))}
                dot={false}
                strokeOpacity={1}
                key={category}
                name={category}
                type="linear"
                dataKey={category}
                stroke=""
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                isAnimationActive={false}
                connectNulls={connectNulls}
              />
            ))}
          </RechartsLineChart>
        </ResponsiveContainer>
      </div>
    );
  },
);
SparkLineChart.displayName = "SparkLineChart";

//#region SparkBarChart

interface SparkBarChartProps extends React.HTMLAttributes<HTMLDivElement> {
  data: Record<string, unknown>[];
  index: string;
  categories: string[];
  colors?: AvailableChartColorsKeys[];
  autoMinValue?: boolean;
  minValue?: number;
  maxValue?: number;
  barCategoryGap?: string | number;
  type?: "default" | "stacked" | "percent";
}

export const SparkBarChart = React.forwardRef<HTMLDivElement, SparkBarChartProps>(
  (props, forwardedRef) => {
    const {
      data = [],
      categories = [],
      index,
      colors = AvailableChartColors,
      autoMinValue = false,
      minValue,
      maxValue,
      barCategoryGap,
      type = "default",
      className,
      ...other
    } = props;

    const categoryColors = constructCategoryColors(categories, colors);
    const yAxisDomain = getYAxisDomain(autoMinValue, minValue, maxValue);
    const stacked = type === "stacked" || type === "percent";

    return (
      <div ref={forwardedRef} className={cn("h-12 w-28", className)} {...other}>
        <ResponsiveContainer>
          <RechartsBarChart
            data={data}
            margin={{ bottom: 1, left: 1, right: 1, top: 1 }}
            stackOffset={type === "percent" ? "expand" : undefined}
            barCategoryGap={barCategoryGap}
          >
            <XAxis hide dataKey={index} />
            <YAxis hide domain={yAxisDomain as AxisDomain} />
            {categories.map((category) => (
              <Bar
                className={cn(getColorClassName(categoryColors.get(category) as AvailableChartColorsKeys, "fill"))}
                key={category}
                name={category}
                type="linear"
                dataKey={category}
                stackId={stacked ? "stack" : undefined}
                isAnimationActive={false}
                fill=""
              />
            ))}
          </RechartsBarChart>
        </ResponsiveContainer>
      </div>
    );
  },
);
SparkBarChart.displayName = "SparkBarChart";
