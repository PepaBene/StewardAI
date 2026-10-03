import type {
  BusinessNetwork,
  ConcentrationPayload,
  ConcentrationPeriod,
  Contradiction,
  Deal,
  DealsPayload,
  DealStep,
  EvidenceTier,
  FilingHit,
  KnockOnLink,
  KnockOnPayload,
  KpiLink,
  NetworkDisclosure,
  NetworkEdge,
  NetworkEvent,
  Parsed,
  PatentFamily,
  PatentOverlapRow,
  PatentTrendRow,
  ProForma,
  Reconciliation,
  ResearchObservation,
  ReviewQueue,
  RowStatus,
  SuggestedDisclosure,
} from "./types";

/*
 * Runtime guards for untyped endpoints. A response that does not have the expected shape is
 * rejected as a whole (`{ ok: false }`), which the views show as "unavailable" — never as an
 * empty graph or zero counts.
 */

class Reject extends Error {}

function fail(path: string, expected: string): never {
  throw new Reject(`${path}: expected ${expected}`);
}

type Rec = Record<string, unknown>;

export function isRecord(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function rec(v: unknown, path: string): Rec {
  return isRecord(v) ? v : fail(path, "an object");
}

function arr(v: unknown, path: string): unknown[] {
  return Array.isArray(v) ? v : fail(path, "a list");
}

function reqStr(v: unknown, path: string): string {
  return typeof v === "string" && v !== "" ? v : fail(path, "a non-empty string");
}

function optStr(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

/** A finite number, or a numeric string (Decimals may arrive either way); otherwise null. */
export function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function reqInt(v: unknown, path: string): number {
  const n = num(v);
  return n !== null && Number.isInteger(n) ? n : fail(path, "an integer");
}

function optRecord(v: unknown): Rec {
  if (isRecord(v)) return v;
  if (typeof v === "string") {
    try {
      const parsed: unknown = JSON.parse(v);
      return isRecord(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }
  return {};
}

function strList(v: unknown): string[] {
  let value = v;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : [];
}

export function toStatus(v: unknown): RowStatus {
  return v === "confirmed" || v === "suggested" || v === "disputed" || v === "rejected" ? v : "unrecognised";
}

const TIERS: readonly EvidenceTier[] = ["primary", "regulatory", "owner_review", "vendor", "community", "candidate", "unverified"];
export function toTier(v: unknown): EvidenceTier {
  return typeof v === "string" && (TIERS as readonly string[]).includes(v) ? (v as EvidenceTier) : "unknown";
}

function guard<T>(fn: () => T): Parsed<T> {
  try {
    return { ok: true, value: fn() };
  } catch (e) {
    if (e instanceof Reject) return { ok: false, reason: e.message };
    return { ok: false, reason: "unexpected response shape" };
  }
}

function parseEdge(raw: unknown, i: number): NetworkEdge {
  const p = `edges[${i}]`;
  const r = rec(raw, p);
  const idRaw = r.id;
  let id: string;
  let rowId: number | null;
  let legacy = false;
  if (typeof idRaw === "number" && Number.isInteger(idRaw)) {
    id = String(idRaw);
    rowId = idRaw;
  } else if (typeof idRaw === "string" && /^legacy:\d+$/.test(idRaw)) {
    id = idRaw;
    rowId = Number(idRaw.slice(7));
    legacy = true;
  } else if (typeof idRaw === "string" && /^\d+$/.test(idRaw)) {
    id = idRaw;
    rowId = Number(idRaw);
  } else {
    return fail(`${p}.id`, "an integer or legacy:<n>");
  }
  return {
    id,
    rowId,
    legacy,
    subjectKey: optStr(r.subject_key) ?? "",
    objectKey: optStr(r.object_key) ?? "",
    objectName: reqStr(r.object_name ?? r.object_key, `${p}.object_name`),
    edgeType: reqStr(r.edge_type, `${p}.edge_type`),
    direction: optStr(r.direction) ?? "outgoing",
    status: toStatus(r.status),
    validFrom: optStr(r.valid_from),
    validTo: optStr(r.valid_to),
    tier: toTier(r.evidence_tier),
    source: optStr(r.source) ?? "unknown source",
    sourceUrl: optStr(r.source_url),
    evidenceId: num(r.evidence_id),
    passage: optStr(r.passage),
    knownAt: optStr(r.known_at),
    decisionReason: optStr(r.decision_reason),
    detail: optRecord(r.detail),
  };
}

function parseEvent(raw: unknown, i: number): NetworkEvent {
  const p = `events[${i}]`;
  const r = rec(raw, p);
  return {
    id: reqInt(r.id, `${p}.id`),
    eventType: reqStr(r.event_type, `${p}.event_type`),
    eventState: optStr(r.event_state) ?? "unknown",
    announcedAt: optStr(r.announced_at),
    effectiveAt: optStr(r.effective_at),
    knownAt: optStr(r.known_at),
    source: optStr(r.source) ?? "unknown source",
    sourceUrl: optStr(r.source_url),
    evidenceId: num(r.evidence_id),
    counterpartyKey: optStr(r.counterparty_key),
    terms: optRecord(r.terms),
    status: toStatus(r.status),
    thesisKpi: optStr(r.thesis_kpi),
    detail: optRecord(r.detail),
  };
}

function parseDisclosure(raw: unknown, i: number): NetworkDisclosure {
  const p = `disclosures[${i}]`;
  const r = rec(raw, p);
  return {
    id: reqInt(r.id, `${p}.id`),
    relationship: reqStr(r.relationship, `${p}.relationship`),
    direction: optStr(r.direction) ?? "unknown",
    counterpartyKey: optStr(r.counterparty_key),
    counterpartyName: optStr(r.counterparty_name),
    anonymous: r.anonymous_counterparty === true,
    reportedShare: num(r.reported_share),
    shareBasis: optStr(r.share_basis),
    fiscalPeriod: optStr(r.fiscal_period),
    validFrom: optStr(r.valid_from),
    validTo: optStr(r.valid_to),
    evidenceId: num(r.evidence_id),
    passage: optStr(r.passage),
    sourceUrl: optStr(r.source_url),
    source: optStr(r.source) ?? "unknown source",
    status: toStatus(r.status),
    decisionReason: optStr(r.decision_reason),
    knownAt: optStr(r.known_at),
  };
}

/**
 * EPO OPS bibliographic data carries no forward citations: the backend stores 0 for them. A 0 from
 * that source is "not provided", not "uncited".
 */
export function forwardCitations(value: number | null, source: string | null): number | null {
  return value === 0 && source !== null && source.toLowerCase().startsWith("epo_ops") ? null : value;
}

function parsePatent(raw: unknown, i: number): PatentFamily {
  const p = `patents[${i}]`;
  const r = rec(raw, p);
  return {
    familyKey: reqStr(r.family_key, `${p}.family_key`),
    priorityDate: optStr(r.priority_date),
    firstPublicationDate: optStr(r.first_publication_date),
    title: optStr(r.family_title),
    cpcCodes: strList(r.cpc_codes),
    jurisdictions: strList(r.jurisdictions),
    citationsForward: forwardCitations(num(r.citations_forward), optStr(r.source)),
    citationsBackward: num(r.citations_backward),
    legalStatus: optStr(r.legal_status) ?? "unknown",
    source: optStr(r.source) ?? "unknown source",
    sourceUrl: optStr(r.source_url),
    knownAt: optStr(r.known_at),
    applicant: optStr(r.applicant),
    ownershipConfidence: num(r.ownership_confidence),
    ownershipBasis: optStr(r.ownership_basis),
    ownershipValidFrom: optStr(r.ownership_valid_from),
    ownershipValidTo: optStr(r.ownership_valid_to),
    evidenceId: num(r.evidence_id),
    // Absent flag is treated as NOT attributed: never claim an ownership path that was not stated.
    attributed: r.attributed === true,
  };
}

export function parseBusinessNetwork(raw: unknown): Parsed<BusinessNetwork> {
  return guard(() => {
    const r = rec(raw, "body");
    return {
      ticker: reqStr(r.ticker, "ticker"),
      knownAsOf: optStr(r.known_as_of),
      edges: arr(r.edges, "edges").map(parseEdge),
      events: arr(r.events, "events").map(parseEvent),
      disclosures: arr(r.disclosures, "disclosures").map(parseDisclosure),
      patents: arr(r.patents, "patents").map(parsePatent),
      limitations: strList(r.limitations),
    };
  });
}

function parseStep(raw: unknown, i: number): DealStep {
  const p = `timeline[${i}]`;
  const r = rec(raw, p);
  return {
    eventId: num(r.event_id),
    state: reqStr(r.state, `${p}.state`),
    status: toStatus(r.status),
    date: optStr(r.date),
    knownAt: optStr(r.known_at),
    evidenceId: num(r.evidence_id),
    source: optStr(r.source) ?? "unknown source",
    sourceUrl: optStr(r.source_url),
    tier: toTier(r.evidence_tier),
  };
}

function parseProForma(raw: unknown): ProForma | null {
  if (!isRecord(raw)) return null;
  return {
    currency: optStr(raw.currency),
    sharesIssued: num(raw.shares_issued),
    shareCountIncrease: num(raw.share_count_increase),
    newHoldersOwnership: num(raw.new_holders_ownership),
    addedGrossDebt: num(raw.added_gross_debt),
    netDebtIncrease: num(raw.net_debt_increase),
    grossLeverageBefore: num(raw.gross_leverage_before),
    grossLeverageAfter: num(raw.gross_leverage_after),
    netLeverageBefore: num(raw.net_leverage_before),
    netLeverageAfter: num(raw.net_leverage_after),
    inputsMissing: strList(raw.inputs_missing),
  };
}

function parseDeal(raw: unknown, i: number): Deal {
  const p = `deals[${i}]`;
  const r = rec(raw, p);
  return {
    dealKey: reqStr(r.deal_key, `${p}.deal_key`),
    eventType: optStr(r.event_type) ?? "deal",
    counterpartyKey: optStr(r.counterparty_key),
    state: optStr(r.state),
    confirmedState: optStr(r.confirmed_state),
    confirmedTerms: optRecord(r.confirmed_terms),
    timeline: arr(r.timeline, `${p}.timeline`).map(parseStep),
    ignored: (Array.isArray(r.ignored) ? r.ignored : []).filter(isRecord).map((x) => ({
      eventId: num(x.event_id),
      reason: optStr(x.reason) ?? "ignored",
    })),
    proForma: parseProForma(r.pro_forma),
    proFormaBasis: optStr(r.pro_forma_basis),
    proFormaUnavailable: optStr(r.pro_forma_unavailable),
  };
}

export function parseDeals(raw: unknown): Parsed<DealsPayload> {
  return guard(() => {
    const r = rec(raw, "body");
    return {
      ticker: reqStr(r.ticker, "ticker"),
      knownAsOf: optStr(r.known_as_of),
      deals: arr(r.deals, "deals").map(parseDeal),
      limitations: strList(r.limitations),
    };
  });
}

function parsePeriod(raw: unknown, i: number): ConcentrationPeriod {
  const p = `periods[${i}]`;
  const r = rec(raw, p);
  return {
    fiscalPeriod: reqStr(r.fiscal_period, `${p}.fiscal_period`),
    relationship: reqStr(r.relationship, `${p}.relationship`),
    shareBasis: optStr(r.share_basis) ?? "unknown",
    namedCount: reqInt(r.named_count, `${p}.named_count`),
    anonymousCount: reqInt(r.anonymous_count, `${p}.anonymous_count`),
    namedShareSum: num(r.named_share_sum),
    anonymousShareSum: num(r.anonymous_share_sum),
    reportedShareSum: num(r.reported_share_sum),
    missingShareCount: reqInt(r.missing_share_count, `${p}.missing_share_count`),
    largestShare: num(r.largest_share),
    shareExceedsTotal: r.share_exceeds_total === true,
    openContradictions: num(r.open_contradictions) ?? 0,
  };
}

export function parseConcentration(raw: unknown): Parsed<ConcentrationPayload> {
  return guard(() => {
    const r = rec(raw, "body");
    return {
      ticker: reqStr(r.ticker, "ticker"),
      periods: arr(r.periods, "periods").map(parsePeriod),
      limitations: strList(r.limitations),
    };
  });
}

export function parsePatentTrends(raw: unknown): Parsed<PatentTrendRow[]> {
  return guard(() =>
    arr(raw, "body").map((x, i) => {
      const p = `trend[${i}]`;
      const r = rec(x, p);
      const counts: Record<string, number> = {};
      for (const [k, v] of Object.entries(optRecord(r.legal_status_counts))) {
        const n = num(v);
        if (n !== null) counts[k] = n;
      }
      return {
        cpc: reqStr(r.cpc, `${p}.cpc`),
        year: reqInt(r.year, `${p}.year`),
        families: reqInt(r.families, `${p}.families`),
        forwardCitations: num(r.forward_citations) ?? 0,
        legalStatusCounts: counts,
        medianYearsToGrant: num(r.median_years_to_grant),
        expiringWithin5y: num(r.expiring_within_5y) ?? 0,
        expired: num(r.expired) ?? 0,
        expiryIsEstimated: r.expiry_is_estimated === true,
        interpretation: optStr(r.interpretation) ?? "",
      };
    }),
  );
}

export function parsePatentOverlap(raw: unknown): Parsed<PatentOverlapRow[]> {
  return guard(() => {
    const r = rec(raw, "body");
    return arr(r.peers, "peers").map((x, i) => {
      const p = `peers[${i}]`;
      const row = rec(x, p);
      return {
        peer: reqStr(row.peer, `${p}.peer`),
        peerFamilies: reqInt(row.peer_families, `${p}.peer_families`),
        ownFamilies: reqInt(row.own_families, `${p}.own_families`),
        overlapShare: num(row.overlap_share),
        jaccard: num(row.jaccard),
        sharedSubclasses: strList(row.shared_subclasses),
        interpretation: optStr(row.interpretation) ?? "",
      };
    });
  });
}

export function parseKpiLinks(raw: unknown): Parsed<KpiLink[]> {
  return guard(() => {
    const r = rec(raw, "body");
    return arr(r.links, "links").map((x, i) => {
      const p = `links[${i}]`;
      const l = rec(x, p);
      const v = isRecord(l.latest_verdict) ? l.latest_verdict : null;
      return {
        eventId: reqInt(l.event_id, `${p}.event_id`),
        eventType: reqStr(l.event_type, `${p}.event_type`),
        eventState: optStr(l.event_state) ?? "unknown",
        status: toStatus(l.status),
        date: optStr(l.date),
        kpiId: reqStr(l.kpi_id, `${p}.kpi_id`),
        kpiLabel: typeof l.kpi === "string" ? l.kpi : isRecord(l.kpi) ? optStr(l.kpi.label ?? l.kpi.name) : null,
        kpiUnit: isRecord(l.kpi) ? optStr(l.kpi.unit) : null,
        kpiDefined: l.kpi_defined === true,
        latestVerdict: v
          ? {
              status: optStr(v.status) ?? "unknown",
              observed: num(v.observed_value),
              threshold: num(v.threshold),
              operator: optStr(v.operator),
              fiscalPeriod: optStr(v.fiscal_period),
            }
          : null,
        newsClusters: (Array.isArray(l.news_clusters) ? l.news_clusters : []).filter(isRecord).flatMap((c) => {
          const id = num(c.id);
          return id === null
            ? []
            : [{ id, headline: optStr(c.headline) ?? "(no headline)", status: optStr(c.status), eventDate: optStr(c.event_date) }];
        }),
        // Only the server's own rule (confirmed + linked evidence) may say so.
        canSupportBreach: l.can_support_breach === true,
        sourceUrl: optStr(l.source_url),
      };
    });
  });
}

export function parseObservations(raw: unknown): Parsed<ResearchObservation[]> {
  return guard(() => {
    const r = rec(raw, "body");
    return arr(r.observations, "observations").map((x, i) => {
      const p = `observations[${i}]`;
      const o = rec(x, p);
      return {
        id: reqInt(o.id, `${p}.id`),
        provider: reqStr(o.provider, `${p}.provider`),
        dataset: reqStr(o.dataset, `${p}.dataset`),
        metric: reqStr(o.metric, `${p}.metric`),
        periodEnd: optStr(o.period_end),
        value: num(o.value),
        textValue: optStr(o.text_value),
        unit: optStr(o.unit),
        currency: optStr(o.currency),
        sourceUrl: optStr(o.source_url),
        publishedAt: optStr(o.published_at),
        knownAt: optStr(o.known_at),
        matchConfidence: num(o.match_confidence),
      };
    });
  });
}

export function parseReconciliations(raw: unknown): Parsed<Reconciliation[]> {
  return guard(() => {
    const r = rec(raw, "body");
    return arr(r.open, "open").map((x, i) => {
      const p = `open[${i}]`;
      const o = rec(x, p);
      return {
        id: reqInt(o.id, `${p}.id`),
        ticker: reqStr(o.ticker, `${p}.ticker`),
        changeKind: reqStr(o.change_kind, `${p}.change_kind`),
        fromKey: optStr(o.from_key),
        toKey: optStr(o.to_key),
        observedSource: optStr(o.observed_source),
        observedAt: optStr(o.observed_at),
        outcome: optStr(o.outcome) ?? "unknown",
        dealEventId: num(o.deal_event_id),
      };
    });
  });
}

export function parseReviewQueue(raw: unknown): Parsed<ReviewQueue> {
  return guard(() => {
    const r = rec(raw, "body");
    const hits: FilingHit[] = arr(r.filing_hits, "filing_hits").map((x, i) => {
      const p = `filing_hits[${i}]`;
      const o = rec(x, p);
      return {
        id: reqInt(o.id, `${p}.id`),
        ticker: reqStr(o.ticker, `${p}.ticker`),
        filerName: optStr(o.filer_name),
        form: optStr(o.form),
        filedAt: optStr(o.filed_at),
        url: optStr(o.url),
        reviewState: optStr(o.review_state) ?? "pending",
        reviewNote: optStr(o.review_note),
        attempts: num(o.attempts),
        disclosureId: num(o.disclosure_id),
      };
    });
    const suggested: SuggestedDisclosure[] = arr(r.suggested_disclosures, "suggested_disclosures").map((x, i) => {
      const p = `suggested_disclosures[${i}]`;
      const o = rec(x, p);
      return {
        id: reqInt(o.id, `${p}.id`),
        ticker: reqStr(o.ticker, `${p}.ticker`),
        relationship: reqStr(o.relationship, `${p}.relationship`),
        direction: optStr(o.direction) ?? "unknown",
        counterpartyName: optStr(o.counterparty_name),
        anonymous: o.anonymous_counterparty === true,
        reportedShare: num(o.reported_share),
        shareBasis: optStr(o.share_basis),
        fiscalPeriod: optStr(o.fiscal_period),
        passage: optStr(o.passage),
        sourceUrl: optStr(o.source_url),
        knownAt: optStr(o.known_at),
        origin: optStr(o.origin),
      };
    });
    const contradictions: Contradiction[] = arr(r.contradictions, "contradictions").map((x, i) => {
      const p = `contradictions[${i}]`;
      const o = rec(x, p);
      return {
        id: reqInt(o.id, `${p}.id`),
        ticker: reqStr(o.ticker, `${p}.ticker`),
        relationship: reqStr(o.relationship, `${p}.relationship`),
        fiscalPeriod: optStr(o.fiscal_period),
        kind: optStr(o.kind) ?? "contradiction",
        disclosureId: num(o.disclosure_id),
        otherDisclosureId: num(o.other_disclosure_id),
        createdAt: optStr(o.created_at),
      };
    });
    return { filingHits: hits, suggested, contradictions };
  });
}

export function parseKnockOn(raw: unknown): Parsed<KnockOnPayload> {
  return guard(() => {
    const r = rec(raw, "body");
    const links: KnockOnLink[] = arr(r.links, "links").map((x, i) => {
      const p = `links[${i}]`;
      const o = rec(x, p);
      return {
        ticker: reqStr(o.ticker, `${p}.ticker`),
        counterpartyTicker: reqStr(o.counterparty_ticker, `${p}.counterparty_ticker`),
        relationship: reqStr(o.relationship, `${p}.relationship`),
        direction: optStr(o.direction) ?? "unknown",
        reportedShare: num(o.reported_share),
        shareBasis: optStr(o.share_basis),
        fiscalPeriod: optStr(o.fiscal_period),
        source: optStr(o.source) ?? "",
      };
    });
    return { knownAsOf: optStr(r.known_as_of), links, limitations: strList(r.limitations) };
  });
}
