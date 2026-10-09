import type { Metadata } from "next";

import { BacktestView } from "./backtest-view";

export const metadata: Metadata = { title: "Test a screen" };

export default function BacktestPage() {
  return <BacktestView />;
}
