import { fieldErrors, flattenErrors } from "./validation-errors";

/**
 * A readable message from an API error body. FastAPI sends `detail` as a string for domain
 * errors and as an array of `{loc, msg}` for a 422; `String(detail)` turns the array into
 * "[object Object]". Returns `fallback` when nothing readable is present.
 */
export function errorMessage(error: unknown, fallback = ""): string {
  if (typeof error === "string") return error || fallback;
  if (!error || typeof error !== "object" || !("detail" in error)) return fallback;
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail === "string") return detail || fallback;
  if (Array.isArray(detail)) {
    const rows = flattenErrors(fieldErrors(error));
    if (rows.length > 0) return rows.map((r) => (r.path ? `${r.path}: ${r.message}` : r.message)).join("; ");
  }
  return fallback;
}
