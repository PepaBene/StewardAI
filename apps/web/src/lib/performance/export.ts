import { getBrowserApiClient } from "@/lib/api/client";

export type ExportKind = "ledger" | "lots" | "realized" | "attribution" | "closed_positions" | "positions";

/** One performance table as CSV text, the same rows the pages show. */
export const fetchExport = (kind: ExportKind, days?: number) =>
  getBrowserApiClient().GET("/portfolio/performance/export", {
    params: { query: { kind, ...(days ? { days } : {}) } },
    parseAs: "text",
  });
