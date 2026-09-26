import type { ConfigExport } from "@/lib/api/client";

/*
 * Config export and import files: the name an export is saved under, and a first check of
 * a chosen file before it is sent (the backend validates every domain's body).
 */

/** "stewardai-config-2026-09-25.json" (local date). */
export function exportFileName(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `stewardai-config-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}.json`;
}

export type ParsedImport = { ok: true; file: ConfigExport; domains: string[] } | { ok: false; message: string };

export function parseImportFile(text: string): ParsedImport {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (err) {
    return { ok: false, message: `Not valid JSON: ${err instanceof Error ? err.message : String(err)}` };
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return { ok: false, message: "The file is not a StewardAI config export (expected a JSON object)." };
  }
  const record = data as Record<string, unknown>;
  if (record.format !== undefined && record.format !== "stewardai-config") {
    return { ok: false, message: `Unknown format ${JSON.stringify(record.format)}; expected "stewardai-config".` };
  }
  const domains = record.domains;
  if (typeof domains !== "object" || domains === null || Array.isArray(domains)) {
    return { ok: false, message: "The file has no \"domains\" object. Use a file saved with Export." };
  }
  const names = Object.keys(domains);
  if (names.length === 0) return { ok: false, message: "The file lists no config domains." };
  return { ok: true, file: record as ConfigExport, domains: names };
}
