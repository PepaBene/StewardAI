import type { FactOut } from "@/lib/api/client";

/*
 * Provenance answers a different question from confidence (principle 9 of the Ledger II
 * redesign proposal): not "how sure are we this is right" but "how was this number
 * produced" — verbatim from a filing, a correction the owner entered, or arithmetic
 * computed from stored figures. Neither is inferred from the other, so this never reuses
 * a confidence hue or icon; it's a plain, quiet, mono-font tag.
 */
export type Provenance = "verbatim" | "owner_correction" | "computed";

export const PROVENANCE = {
  verbatim: { label: "Verbatim", description: "Read directly from the source document, as printed." },
  owner_correction: { label: "Owner correction", description: "Entered by the owner; it replaces the extracted value." },
  computed: { label: "Computed", description: "Derived arithmetically from stored figures, not read from a document." },
} as const satisfies Record<Provenance, { label: string; description: string }>;

/** A fact is either read straight off a document, or a correction the owner typed in. */
export function factProvenance(fact: Pick<FactOut, "origin">): Provenance {
  return fact.origin === "owner" ? "owner_correction" : "verbatim";
}
