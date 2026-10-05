/** Defensive readers for backend responses that OpenAPI leaves untyped. Missing stays null, never 0. */
export type Rec = Record<string, unknown>;

export const isRecord = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export const recs = (v: unknown): Rec[] => arr(v).filter(isRecord);

export function titleCase(key: string): string {
  return key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
