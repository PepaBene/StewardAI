import type { ResearchBody, Source } from "@/lib/api/client";

/*
 * The run snapshot (what the run was started with), the working-records purge and the
 * source roles web sources carry. All tolerate old reports that stored none of it.
 */

export type RunSnapshot = NonNullable<ResearchBody["snapshot"]>;

export function shortHash(hash: string | null | undefined, length = 12): string {
  return hash ? hash.slice(0, length) : "—";
}

function text(record: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = record?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

export type SnapshotRows = {
  profile: { id: string | null; name: string | null };
  template: string | null;
  prompts: { key: string; hash: string; chars: number }[];
  versions: { domain: string; id: number | null }[];
  planHash: string;
  digest: string;
  takenAt: string;
};

/** The snapshot as display rows: profile, template, prompt hashes, config version ids, digest. */
export function snapshotRows(snapshot: RunSnapshot, templateId?: string | null): SnapshotRows {
  return {
    profile: { id: text(snapshot.profile, "id"), name: text(snapshot.profile, "name") },
    template: text(snapshot.template, "id") ?? templateId ?? null,
    prompts: Object.entries(snapshot.prompts ?? {})
      .map(([key, copy]) => ({ key, hash: copy.sha256, chars: copy.text.length }))
      .sort((a, b) => a.key.localeCompare(b.key)),
    versions: Object.entries(snapshot.versions ?? {})
      .map(([domain, id]) => ({ domain, id }))
      .sort((a, b) => a.domain.localeCompare(b.domain)),
    planHash: snapshot.plan_sha256,
    digest: snapshot.digest,
    takenAt: snapshot.taken_at,
  };
}

export type PurgeCounts = { urls: number; checkpoints: number; traces: number };

/** "Removed 14 URLs, 6 checkpoints and 3 tool traces." */
export function purgeSummary(counts: PurgeCounts): string {
  const part = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return `Removed ${part(counts.urls, "URL", "URLs")}, ${part(counts.checkpoints, "checkpoint", "checkpoints")} and ${part(counts.traces, "tool trace", "tool traces")}.`;
}

// ---------------------------------------------------------------------------
// Source roles
// ---------------------------------------------------------------------------

const ROLE_LABELS: Record<string, string> = {
  primary: "Primary",
  quality_news: "Quality news",
  secondary: "Secondary",
  owner_trusted: "Owner-trusted",
  forum: "Forum",
};

/** Caveats shown when the stored source carries none of its own. */
const ROLE_CAVEATS: Record<string, string> = {
  owner_trusted: "On your trusted list; it is still not a company or regulator record.",
  forum: "Opinion from a discussion forum; not a source for figures or dates.",
};

export const INJECTION_FLAG = "Low confidence: this page contained instruction-like text. It was treated as data only and never followed.";

export type SourceRoleNote = { roleLabel: string | null; caveat: string | null; flagged: boolean };

/** The role label, caveat and low-confidence flag of a source card; nulls for a stored-record source. */
export function sourceRoleNote(source: Pick<Source, "id" | "role" | "caveat">, flaggedIds: readonly string[] | null | undefined): SourceRoleNote {
  const role = source.role ?? null;
  return {
    roleLabel: role ? (ROLE_LABELS[role] ?? role.replaceAll("_", " ")) : null,
    caveat: source.caveat || (role ? (ROLE_CAVEATS[role] ?? null) : null),
    flagged: (flaggedIds ?? []).includes(source.id),
  };
}
