import type { Metadata } from "next";

import { StrategyEditor } from "./strategy-editor";

export const metadata: Metadata = { title: "Strategy" };

export default function StrategyPage() {
  return <StrategyEditor />;
}
