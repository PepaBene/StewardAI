"use client";

import dynamic from "next/dynamic";

import { ChartEmpty } from "./shared";
import type { ChoroplethProps } from "./choropleth-map";

export type { ChoroplethDatum, ChoroplethExtraRow, ChoroplethProps } from "./choropleth-map";

/**
 * Country choropleth with a ranked list beside it. The map itself (and the ~100 kB world
 * topology it draws) is a separate chunk that loads when a page first renders it, so charts
 * that import this barrel pay nothing for it.
 */
export const Choropleth = dynamic<ChoroplethProps>(() => import("./choropleth-map").then((m) => m.ChoroplethMap), {
  loading: () => <ChartEmpty label="Loading map…" minHeight={240} />,
});
