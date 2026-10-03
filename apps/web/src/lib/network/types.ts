/*
 * Local types for the Phase 5 business-network read models. Most of these endpoints are
 * untyped in OpenAPI (`unknown`), so the shapes below are the ones the backend builds in
 * steward/free_source_reads.py and steward/graph/*; `parse.ts` narrows the raw JSON into
 * them and rejects anything else.
 */

/** Status of a graph row. `unrecognised` is never shown as confirmed. */
export type RowStatus = "confirmed" | "suggested" | "disputed" | "rejected" | "unrecognised";

export type EvidenceTier = "primary" | "regulatory" | "owner_review" | "vendor" | "community" | "candidate" | "unverified" | "unknown";

export type NetworkEdge = {
  /** "12" for a normalised edge, "legacy:5" for an entity_relationship row (GLEIF/Wikidata). */
  id: string;
  /** Numeric id within its own table. */
  rowId: number | null;
  legacy: boolean;
  subjectKey: string;
  objectKey: string;
  objectName: string;
  edgeType: string;
  direction: string;
  status: RowStatus;
  validFrom: string | null;
  validTo: string | null;
  tier: EvidenceTier;
  source: string;
  sourceUrl: string | null;
  evidenceId: number | null;
  passage: string | null;
  knownAt: string | null;
  decisionReason: string | null;
  detail: Record<string, unknown>;
};

export type NetworkEvent = {
  id: number;
  eventType: string;
  eventState: string;
  announcedAt: string | null;
  effectiveAt: string | null;
  knownAt: string | null;
  source: string;
  sourceUrl: string | null;
  evidenceId: number | null;
  counterpartyKey: string | null;
  terms: Record<string, unknown>;
  status: RowStatus;
  thesisKpi: string | null;
  detail: Record<string, unknown>;
};

export type NetworkDisclosure = {
  id: number;
  relationship: string;
  direction: string;
  counterpartyKey: string | null;
  counterpartyName: string | null;
  anonymous: boolean;
  /** Fraction 0..1 as reported by the filer (never imputed). */
  reportedShare: number | null;
  shareBasis: string | null;
  fiscalPeriod: string | null;
  validFrom: string | null;
  validTo: string | null;
  evidenceId: number | null;
  passage: string | null;
  sourceUrl: string | null;
  source: string;
  status: RowStatus;
  decisionReason: string | null;
  knownAt: string | null;
};

export type PatentFamily = {
  familyKey: string;
  priorityDate: string | null;
  firstPublicationDate: string | null;
  title: string | null;
  cpcCodes: string[];
  jurisdictions: string[];
  citationsForward: number | null;
  citationsBackward: number | null;
  legalStatus: string;
  source: string;
  sourceUrl: string | null;
  knownAt: string | null;
  applicant: string | null;
  ownershipConfidence: number | null;
  ownershipBasis: string | null;
  ownershipValidFrom: string | null;
  ownershipValidTo: string | null;
  evidenceId: number | null;
  /** True when a dated ownership path attributes the family to this holding. */
  attributed: boolean;
};

export type BusinessNetwork = {
  ticker: string;
  knownAsOf: string | null;
  edges: NetworkEdge[];
  events: NetworkEvent[];
  disclosures: NetworkDisclosure[];
  patents: PatentFamily[];
  limitations: string[];
};

export type DealStep = {
  eventId: number | null;
  state: string;
  status: RowStatus;
  date: string | null;
  knownAt: string | null;
  evidenceId: number | null;
  source: string;
  sourceUrl: string | null;
  tier: EvidenceTier;
};

export type ProForma = {
  currency: string | null;
  sharesIssued: number | null;
  shareCountIncrease: number | null;
  newHoldersOwnership: number | null;
  addedGrossDebt: number | null;
  netDebtIncrease: number | null;
  grossLeverageBefore: number | null;
  grossLeverageAfter: number | null;
  netLeverageBefore: number | null;
  netLeverageAfter: number | null;
  inputsMissing: string[];
};

export type Deal = {
  dealKey: string;
  eventType: string;
  counterpartyKey: string | null;
  /** State to show the owner (may rest on suggested events). */
  state: string | null;
  /** State resting on confirmed events only: the one analytics may use. */
  confirmedState: string | null;
  confirmedTerms: Record<string, unknown>;
  timeline: DealStep[];
  ignored: { eventId: number | null; reason: string }[];
  proForma: ProForma | null;
  proFormaBasis: string | null;
  proFormaUnavailable: string | null;
};

export type DealsPayload = { ticker: string; knownAsOf: string | null; deals: Deal[]; limitations: string[] };

export type ConcentrationPeriod = {
  fiscalPeriod: string;
  relationship: string;
  shareBasis: string;
  namedCount: number;
  anonymousCount: number;
  namedShareSum: number | null;
  anonymousShareSum: number | null;
  reportedShareSum: number | null;
  missingShareCount: number;
  /** Per-side counts of disclosures with no reported share; null when the API did not send them (older backend). */
  namedMissingShareCount: number | null;
  anonymousMissingShareCount: number | null;
  /** Sums over reported shares only; null = nothing reported on that side (never 0). */
  namedReportedShareSum: number | null;
  anonymousReportedShareSum: number | null;
  totalReportedShareSum: number | null;
  /** True when the side's sum is a lower bound (some disclosure on that side has no share); null when not sent. */
  namedAtLeast: boolean | null;
  anonymousAtLeast: boolean | null;
  totalAtLeast: boolean | null;
  largestShare: number | null;
  shareExceedsTotal: boolean;
  openContradictions: number;
};

export type ConcentrationPayload = { ticker: string; periods: ConcentrationPeriod[]; limitations: string[] };

export type PatentTrendRow = {
  cpc: string;
  year: number;
  families: number;
  /** null = no family in the bucket has citation data (unknown), never 0. */
  forwardCitations: number | null;
  familiesWithCitationData: number | null;
  legalStatusCounts: Record<string, number>;
  medianYearsToGrant: number | null;
  expiringWithin5y: number;
  expired: number;
  expiryIsEstimated: boolean;
  interpretation: string;
};

export type PatentOverlapRow = {
  peer: string;
  peerFamilies: number;
  ownFamilies: number;
  overlapShare: number | null;
  jaccard: number | null;
  sharedSubclasses: string[];
  interpretation: string;
};

export type KpiLink = {
  eventId: number;
  eventType: string;
  eventState: string;
  status: RowStatus;
  date: string | null;
  kpiId: string;
  kpiLabel: string | null;
  kpiDefined: boolean;
  latestVerdict: { status: string; observed: number | null; threshold: number | null; operator: string | null; fiscalPeriod: string | null } | null;
  /** Unit of the KPI when the thesis states one. */
  kpiUnit: string | null;
  newsClusters: { id: number; headline: string; status: string | null; eventDate: string | null }[];
  /** Only a confirmed event with linked primary evidence may support a thesis breach. */
  canSupportBreach: boolean;
  sourceUrl: string | null;
};

export type ResearchObservation = {
  id: number;
  provider: string;
  dataset: string;
  metric: string;
  periodEnd: string | null;
  value: number | null;
  textValue: string | null;
  unit: string | null;
  currency: string | null;
  sourceUrl: string | null;
  publishedAt: string | null;
  knownAt: string | null;
  matchConfidence: number | null;
};

export type Reconciliation = {
  id: number;
  ticker: string;
  changeKind: string;
  fromKey: string | null;
  toKey: string | null;
  observedSource: string | null;
  observedAt: string | null;
  outcome: string;
  dealEventId: number | null;
};

export type FilingHit = {
  id: number;
  ticker: string;
  filerName: string | null;
  form: string | null;
  filedAt: string | null;
  url: string | null;
  reviewState: string;
  reviewNote: string | null;
  attempts: number | null;
  disclosureId: number | null;
};

export type SuggestedDisclosure = {
  id: number;
  ticker: string;
  relationship: string;
  direction: string;
  counterpartyName: string | null;
  anonymous: boolean;
  reportedShare: number | null;
  shareBasis: string | null;
  fiscalPeriod: string | null;
  passage: string | null;
  sourceUrl: string | null;
  knownAt: string | null;
  origin: string | null;
};

export type Contradiction = {
  id: number;
  ticker: string;
  relationship: string;
  fiscalPeriod: string | null;
  kind: string;
  disclosureId: number | null;
  otherDisclosureId: number | null;
  createdAt: string | null;
};

export type ReviewQueue = { filingHits: FilingHit[]; suggested: SuggestedDisclosure[]; contradictions: Contradiction[] };

export type KnockOnLink = {
  ticker: string;
  counterpartyTicker: string;
  relationship: string;
  direction: string;
  reportedShare: number | null;
  shareBasis: string | null;
  fiscalPeriod: string | null;
  source: string;
};

export type KnockOnPayload = { knownAsOf: string | null; links: KnockOnLink[]; limitations: string[] };

export type Parsed<T> = { ok: true; value: T } | { ok: false; reason: string };
