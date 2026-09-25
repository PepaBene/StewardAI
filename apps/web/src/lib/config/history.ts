import type { ConfigVersionMeta, DiffChange } from "@/lib/api/client";

import { domainLabel } from "./domains";

/*
 * Pure helpers for the config history view: which version to compare a selected one with,
 * how to show a diff entry, and how to read the 409 / 422 error lists the API returns.
 */

/**
 * The version to diff `selectedId` against by default: its parent, or else the next older
 * version in the list (newest first). Null for the first version.
 */
export function defaultFrom(versions: ConfigVersionMeta[], selectedId: number): number | null {
  const index = versions.findIndex((v) => v.id === selectedId);
  if (index < 0) return null;
  const parent = versions[index].parent_id;
  if (parent !== null && versions.some((v) => v.id === parent)) return parent;
  return versions[index + 1]?.id ?? null;
}

/** The active version, or the newest one. */
export function initialSelection(versions: ConfigVersionMeta[]): number | null {
  return (versions.find((v) => v.active) ?? versions[0])?.id ?? null;
}

export const OP_LABELS: Record<DiffChange["op"], string> = {
  add: "Added",
  remove: "Removed",
  replace: "Changed",
};

/** "/target_positions/0/band" -> "target_positions › 0 › band" (JSON pointer escapes decoded). */
export function formatPath(path: string): string {
  if (path === "" || path === "/") return "(whole body)";
  return path
    .replace(/^\//, "")
    .split("/")
    .map((p) => p.replaceAll("~1", "/").replaceAll("~0", "~"))
    .join(" › ");
}

/** A diff value as short JSON text; undefined (absent) shows as an em dash. */
export function formatValue(value: unknown): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return JSON.stringify(value);
  return JSON.stringify(value, null, 2);
}

export type ListedError = { domain: string | null; path: string; message: string };

type Loc = (string | number)[];

/**
 * The `detail` of a 409 (rollback) or 422 (import) answer as a flat list. Import errors
 * carry `loc` ["domains", <domain>, "body", ...field path]; FastAPI's own request errors
 * start with "body". A string detail becomes one entry.
 */
export function listErrors(error: unknown): ListedError[] {
  const detail = error && typeof error === "object" && "detail" in error ? (error as { detail: unknown }).detail : error;
  if (typeof detail === "string") return [{ domain: null, path: "", message: detail }];
  if (!Array.isArray(detail)) return [];
  return detail.map((item: { loc?: Loc; msg?: unknown }) => {
    let loc: Loc = Array.isArray(item.loc) ? [...item.loc] : [];
    if (loc[0] === "body") loc = loc.slice(1);
    let domain: string | null = null;
    if (loc[0] === "domains" && typeof loc[1] === "string") {
      domain = domainLabel(loc[1]);
      loc = loc.slice(loc[2] === "body" ? 3 : 2);
    }
    const path = loc.filter((p) => typeof p === "number" || !String(p).includes("[")).join(".");
    return { domain, path, message: String(item.msg ?? "Invalid value").replace(/^Value error, /, "") };
  });
}

/** Errors grouped by domain, in first-seen order. */
export function groupByDomain(errors: ListedError[]): { domain: string | null; errors: ListedError[] }[] {
  const groups = new Map<string | null, ListedError[]>();
  for (const e of errors) {
    const list = groups.get(e.domain) ?? [];
    list.push(e);
    groups.set(e.domain, list);
  }
  return [...groups].map(([domain, list]) => ({ domain, errors: list }));
}
