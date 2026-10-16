import type { components } from "@/lib/api/schema";
import { jobTypeLabel } from "@/lib/jobs/job-detail";
import { humanise } from "@/lib/labels";
import type { AiDecisions } from "@/lib/reference/ai-decisions";

/*
 * The unified Inbox (plan §5.6, R3): every queue that waits for the owner, normalised to one
 * row shape so the All view can group by company and kind. Each source keeps its own full
 * view under /inbox?kind=…; this module only reads and normalises (pure, tested).
 */

type S = components["schemas"];

export const INBOX_KINDS = [
  "proposals",
  "needs-you",
  "escalations",
  "figures",
  "relationships",
  "disputed",
  "disclosure-alerts",
  "deal-parties",
  "ai-decisions",
  "stopped-jobs",
  "accuracy",
] as const;

export type InboxKind = (typeof INBOX_KINDS)[number];

export const KIND_LABELS: Record<InboxKind, string> = {
  proposals: "Proposals",
  "needs-you": "Autopilot asks",
  escalations: "Impact notes",
  figures: "Figures to check",
  relationships: "Relationships",
  disputed: "Disputed stories",
  "disclosure-alerts": "Filing alerts",
  "deal-parties": "Deal parties",
  "ai-decisions": "AI decisions to check",
  "stopped-jobs": "Stopped jobs",
  accuracy: "Accuracy checks",
};

export function isInboxKind(v: string | null | undefined): v is InboxKind {
  return !!v && (INBOX_KINDS as readonly string[]).includes(v);
}

export type QueueRow = {
  /** `${kind}:${source id}`, unique across queues. */
  id: string;
  kind: InboxKind;
  ticker: string | null;
  /** Company name when the source carries one; the page fills the rest from holdings. */
  name: string | null;
  headline: string;
  why: string | null;
  createdAt: string | null;
  ageDays: number | null;
  href: string;
  /** Rows sharing a key fold into one batch card when there are enough of them. */
  batchKey: string | null;
};

export type QueueSources = {
  proposals?: readonly S["InboxItem"][];
  needsYou?: S["NeedsYou"];
  escalations?: readonly S["EscalationOut"][];
  figures?: readonly S["ReviewItem"][];
  relationships?: S["ReviewQueueResponse"];
  disputed?: readonly S["DisputedStory"][];
  alerts?: readonly S["DisclosureAlertOut"][];
  parties?: readonly S["PartySuggestionOut"][];
  aiDecisions?: AiDecisions;
  /** Failed jobs; only the last STOPPED_JOB_DAYS days count as waiting. */
  failedJobs?: readonly S["JobListItem"][];
};

export const STOPPED_JOB_DAYS = 7;

const DAY = 86_400_000;

export function ageInDays(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Math.max(0, Math.floor((now - t) / DAY)) : null;
}

const inbox = (kind: InboxKind, extra = "") => `/inbox?kind=${kind}${extra}`;

/** Every source's open items as rows, newest first. Missing sources contribute nothing. */
export function toQueueRows(src: QueueSources, now: number): QueueRow[] {
  const rows: QueueRow[] = [];
  const push = (r: Omit<QueueRow, "ageDays"> & { ageDays?: number | null }) =>
    rows.push({ ...r, ageDays: r.ageDays ?? ageInDays(r.createdAt, now) });

  for (const p of src.proposals ?? []) {
    push({
      id: `proposals:${p.id}`,
      kind: "proposals",
      ticker: p.ticker,
      name: null,
      headline: p.headline,
      why: null,
      createdAt: p.created_at,
      href: inbox("proposals", `&item=${p.id}`),
      batchKey: `proposals:${p.trigger_kind}`,
    });
  }
  for (const n of src.needsYou?.items ?? []) {
    push({
      id: `needs-you:${n.kind}:${n.id}`,
      kind: "needs-you",
      ticker: n.ticker,
      name: null,
      headline: n.label,
      why: n.recommendation || n.reasons || null,
      createdAt: null,
      ageDays: n.age_days,
      href: n.href,
      batchKey: `needs-you:${n.kind}`,
    });
  }
  for (const e of src.escalations ?? []) {
    push({
      id: `escalations:${e.id}`,
      kind: "escalations",
      ticker: e.ticker,
      name: null,
      headline: e.title,
      why: null,
      createdAt: e.created_at,
      href: inbox("escalations", `&item=${e.id}`),
      batchKey: null,
    });
  }
  for (const f of src.figures ?? []) {
    push({
      id: `figures:${f.fact.id}`,
      kind: "figures",
      ticker: f.ticker,
      name: f.name,
      headline: `Check ${humanise(f.fact.metric)}`,
      why: f.why,
      createdAt: f.fact.extracted_at,
      href: inbox("figures", `&ticker=${encodeURIComponent(f.ticker)}`),
      batchKey: "figures",
    });
  }
  const rel = src.relationships;
  for (const h of rel?.filing_hits ?? []) {
    push({
      id: `relationships:hit:${h.id}`,
      kind: "relationships",
      ticker: h.ticker,
      name: null,
      headline: `Filing to read${h.form ? ` (${h.form})` : ""}${h.filer_name ? ` from ${h.filer_name}` : ""}`,
      why: h.review_note ?? null,
      createdAt: h.filed_at ?? null,
      href: inbox("relationships", `&ticker=${encodeURIComponent(h.ticker)}`),
      batchKey: "relationships:hits",
    });
  }
  for (const d of rel?.suggested_disclosures ?? []) {
    push({
      id: `relationships:disclosure:${d.id}`,
      kind: "relationships",
      ticker: d.ticker,
      name: null,
      headline: `Confirm ${humanise(d.relationship)}${d.counterparty_name ? `: ${d.counterparty_name}` : ""}`,
      why: d.fiscal_period ? `Reported for ${d.fiscal_period}` : null,
      createdAt: null,
      href: inbox("relationships", `&ticker=${encodeURIComponent(d.ticker)}`),
      batchKey: "relationships:suggested",
    });
  }
  for (const c of rel?.contradictions ?? []) {
    push({
      id: `relationships:contradiction:${c.id}`,
      kind: "relationships",
      ticker: c.ticker,
      name: null,
      headline: `Two sources disagree${c.relationship ? ` on ${humanise(c.relationship)}` : ""}`,
      why: humanise(c.kind),
      createdAt: c.created_at,
      href: inbox("relationships", `&ticker=${encodeURIComponent(c.ticker)}`),
      batchKey: null,
    });
  }
  for (const s of src.disputed ?? []) {
    push({
      id: `disputed:${s.id}`,
      kind: "disputed",
      ticker: s.ticker,
      name: null,
      headline: s.headline,
      why: s.status_reason,
      createdAt: s.first_seen,
      href: inbox("disputed"),
      batchKey: null,
    });
  }
  for (const a of src.alerts ?? []) {
    if (a.acknowledged_at) continue;
    push({
      id: `disclosure-alerts:${a.id}`,
      kind: "disclosure-alerts",
      ticker: a.ticker,
      name: null,
      headline: a.headline,
      why: humanise(a.kind),
      createdAt: a.created_at,
      href: inbox("disclosure-alerts", `&ticker=${encodeURIComponent(a.ticker)}`),
      batchKey: `disclosure-alerts:${a.kind}`,
    });
  }
  for (const p of src.parties ?? []) {
    push({
      id: `deal-parties:${p.participant_id}`,
      kind: "deal-parties",
      ticker: null,
      name: null,
      headline: `Who is ${p.name}? (${humanise(p.role)})`,
      why: p.basis ?? (p.note || null),
      createdAt: null,
      href: inbox("deal-parties"),
      batchKey: "deal-parties",
    });
  }

  const ai = src.aiDecisions;
  if (ai) {
    const decision = (id: string, ticker: string, headline: string, why: string | null, at: string | null, group: string) =>
      push({
        id: `ai-decisions:${id}`,
        kind: "ai-decisions",
        ticker,
        name: null,
        headline,
        why: why || null,
        createdAt: at || null,
        href: inbox("ai-decisions"),
        batchKey: `ai-decisions:${group}`,
      });
    for (const l of ai.listings) decision(`listing:${l.ticker}:${l.decidedAt}`, l.ticker, `Listing ${humanise(l.status).toLowerCase()}`, l.reason, l.decidedAt, "listings");
    for (const p of ai.peers) decision(`peer:${p.id}`, p.ticker, `Peer ${p.peer} ${humanise(p.status).toLowerCase()}`, p.reason, p.at, "peers");
    for (const f of ai.facts) decision(`fact:${f.id}`, f.ticker, `${humanise(f.slot)} set to ${f.value}`, null, f.retrievedAt, "facts");
    for (const d of ai.disclosures)
      decision(`disclosure:${d.id}`, d.ticker, `${humanise(d.relationship)}${d.counterparty ? `: ${d.counterparty}` : ""}`, d.reason, d.at, "disclosures");
    for (const e of ai.exposures) decision(`exposure:${e.ticker}:${e.kind}:${e.name}`, e.ticker, `${humanise(e.kind)} exposure: ${e.name}`, e.quote, null, "exposures");
  }
  for (const j of src.failedJobs ?? []) {
    const age = ageInDays(j.finished_at ?? j.created_at, now);
    if (age === null || age > STOPPED_JOB_DAYS) continue;
    const ticker = typeof j.params?.ticker === "string" ? j.params.ticker : null;
    push({
      id: `stopped-jobs:${j.id}`,
      kind: "stopped-jobs",
      ticker,
      name: null,
      headline: `${jobTypeLabel(j.job_type)} stopped`,
      why: j.error,
      createdAt: j.finished_at ?? j.created_at,
      href: `/jobs/${j.id}`,
      batchKey: `stopped-jobs:${j.job_type}`,
    });
  }

  return rows.sort((a, b) => (b.ageDays ?? -1) - (a.ageDays ?? -1));
}

export function countByKind(rows: readonly QueueRow[]): Record<InboxKind, number> {
  const out = Object.fromEntries(INBOX_KINDS.map((k) => [k, 0])) as Record<InboxKind, number>;
  for (const r of rows) out[r.kind] += 1;
  return out;
}

/** A batch folds this many or more rows with one key into one card. */
export const BATCH_MIN = 3;

export type QueueEntry =
  | { type: "row"; row: QueueRow }
  | { type: "batch"; key: string; kind: InboxKind; rows: QueueRow[] };

export type CompanyGroup = { ticker: string | null; name: string | null; entries: QueueEntry[] };

/**
 * The All view: repeated prompts first fold into batch cards (across companies, since
 * "Write down why you bought 6 holdings" is one task), then the rest group by company,
 * oldest waiting company first, rows inside ordered by kind.
 */
export function groupQueue(rows: readonly QueueRow[]): { batches: QueueEntry[]; companies: CompanyGroup[] } {
  const byKey = new Map<string, QueueRow[]>();
  for (const r of rows) if (r.batchKey) byKey.set(r.batchKey, [...(byKey.get(r.batchKey) ?? []), r]);
  const batched = new Set<string>();
  const batches: QueueEntry[] = [];
  for (const [key, list] of byKey) {
    if (list.length < BATCH_MIN) continue;
    list.forEach((r) => batched.add(r.id));
    batches.push({ type: "batch", key, kind: list[0].kind, rows: list });
  }

  const groups = new Map<string, CompanyGroup>();
  for (const r of rows) {
    if (batched.has(r.id)) continue;
    const k = r.ticker ?? "";
    const g = groups.get(k) ?? { ticker: r.ticker, name: r.name, entries: [] };
    g.name ??= r.name;
    g.entries.push({ type: "row", row: r });
    groups.set(k, g);
  }
  const kindOrder = (e: QueueEntry) => INBOX_KINDS.indexOf(e.type === "row" ? e.row.kind : e.kind);
  const oldest = (g: CompanyGroup) =>
    Math.max(-1, ...g.entries.map((e) => (e.type === "row" ? (e.row.ageDays ?? -1) : -1)));
  const companies = [...groups.values()]
    .map((g) => ({ ...g, entries: [...g.entries].sort((a, b) => kindOrder(a) - kindOrder(b)) }))
    // Company-less rows (deal parties, some autopilot asks) go last.
    .sort((a, b) => (a.ticker === null ? 1 : 0) - (b.ticker === null ? 1 : 0) || oldest(b) - oldest(a));
  return { batches, companies };
}

/** Ageing edge: a week waiting is a warning, two weeks a loss. */
export function ageTone(days: number | null): "none" | "warning" | "loss" {
  if (days === null) return "none";
  if (days >= 14) return "loss";
  if (days >= 7) return "warning";
  return "none";
}
