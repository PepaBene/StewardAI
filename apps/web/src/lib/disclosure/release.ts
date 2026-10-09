import type { CoverageStage } from "@/lib/disclosure/coverage";

/*
 * Release order (plan P9.7): each dataset page is released only when the coverage report's release stage
 * that holds its dataset has met its accuracy gates. Until then the page says so.
 */

export type ReleasePage = "ownership" | "insiders" | "network" | "deals" | "estimates";

/** The coverage-report dataset key behind each page. */
export const PAGE_DATASET: Record<ReleasePage, string> = {
  ownership: "ownership",
  insiders: "insiders",
  network: "dependencies",
  deals: "deals",
  estimates: "estimates",
};

export type ReleaseStatus = { released: boolean; stage: string; state: CoverageStage["state"] } | null;

/** The stage holding this page's dataset; null when the report lists none (nothing to say either way). */
export function releaseStatus(stages: readonly CoverageStage[], page: ReleasePage): ReleaseStatus {
  const stage = stages.find((s) => s.datasets.includes(PAGE_DATASET[page]));
  return stage ? { released: stage.state === "gates_met", stage: stage.stage, state: stage.state } : null;
}

export function releaseNote(s: NonNullable<ReleaseStatus>): string {
  return s.state === "not_started" ? `Not released: the accuracy gate for "${s.stage}" has not been started.` : `Not released: the accuracy gate for "${s.stage}" is open.`;
}
