/*
 * Concise and detailed views of one stored research report. The API serves both
 * (`GET /research/{id}?view=`); the concise one drops detail-only sections and caps the
 * sentences per section, so its section positions differ from the stored report's.
 */

export type ReportView = "concise" | "detailed";

export const REPORT_VIEWS: readonly { id: ReportView; label: string; description: string }[] = [
  { id: "concise", label: "Concise", description: "The key sentences of each section; detail-only sections are left out." },
  { id: "detailed", label: "Detailed", description: "The whole stored report." },
];

/** A stored or typed view name; anything but "concise" is the detailed view. */
export function normaliseView(value: string | null | undefined): ReportView {
  return value === "concise" ? "concise" : "detailed";
}

/**
 * The position of a displayed section in the stored (detailed) report. Feedback on a
 * sentence is addressed by stored position, which a concise view shifts by dropping sections.
 */
export function originalSectionIndex(full: readonly { id: string }[], id: string, shownIndex: number): number {
  const found = full.findIndex((s) => s.id === id);
  return found >= 0 ? found : shownIndex;
}
