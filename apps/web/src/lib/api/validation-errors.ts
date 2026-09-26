/*
 * FastAPI answers an invalid body with 422 and `detail: [{loc, msg, type}]`, one entry per
 * Pydantic error. These helpers turn that into messages keyed by a dotted field path
 * ("theses.0.kpis.1.unit"), so a form can show each error next to its field.
 */

export type FieldErrors = Record<string, string[]>;

type PydanticError = { loc?: unknown[]; msg?: unknown };

/** Field path of a Pydantic `loc`, without the leading "body" and union-member tags. */
export function errorPath(loc: unknown[]): string {
  const parts = loc.filter((part, i) => !(i === 0 && part === "body"));
  return parts
    .filter((part) => typeof part === "number" || (typeof part === "string" && !part.includes("[")))
    .map(String)
    .join(".");
}

export function fieldErrors(error: unknown): FieldErrors {
  const detail = error && typeof error === "object" && "detail" in error ? error.detail : null;
  const errors: FieldErrors = {};
  if (typeof detail === "string") {
    errors[""] = [detail];
    return errors;
  }
  if (!Array.isArray(detail)) return errors;
  for (const item of detail as PydanticError[]) {
    const path = Array.isArray(item.loc) ? errorPath(item.loc) : "";
    const message = String(item.msg ?? "Invalid value").replace(/^Value error, /, "");
    (errors[path] ??= []).push(message);
  }
  return errors;
}

/** Errors at `path` itself (not its children). */
export function errorsAt(errors: FieldErrors, path: string): string[] {
  return errors[path] ?? [];
}

/** Every error message with its path, for a summary list. */
export function flattenErrors(errors: FieldErrors): { path: string; message: string }[] {
  return Object.entries(errors).flatMap(([path, messages]) =>
    messages.map((message) => ({ path, message })),
  );
}

/**
 * Errors of a keyed field's children ("macro_surprise_bands.hicp") folded into the field
 * itself as "hicp: message", so a text area edited as "key value" lines shows them. Other
 * paths are kept as they are.
 */
export function foldChildErrors(errors: FieldErrors, path: string): FieldErrors {
  const folded: FieldErrors = {};
  const own: string[] = [...(errors[path] ?? [])];
  for (const [p, messages] of Object.entries(errors)) {
    if (p === path) continue;
    if (p.startsWith(`${path}.`)) own.push(...messages.map((m) => `${p.slice(path.length + 1)}: ${m}`));
    else folded[p] = messages;
  }
  if (own.length > 0) folded[path] = own;
  return folded;
}
