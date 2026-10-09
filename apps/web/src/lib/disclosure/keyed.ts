/*
 * Results keyed by the inputs that produced them. A page that fetches for a ticker and a date keeps the
 * answer together with the key it was asked for, and shows it only while the key still matches. So a
 * failed refetch can never leave the previous date's or ticker's data on screen under the new inputs,
 * and a failure is an explicit state rather than "Loading…" forever.
 */

export type Loaded<T> = { key: string; data: T | null; error: string | null };
export type Keyed<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };
export type Outcome<T> = { data: T } | { error: string };

/** What the page shows for `key`: null when nothing is asked for, "loading" when the stored result is for other inputs. */
export function resolveLoaded<T>(result: Loaded<T> | null, key: string | null): Keyed<T> | null {
  if (key === null) return null;
  if (result === null || result.key !== key) return { state: "loading" };
  if (result.error !== null) return { state: "error", message: result.error };
  if (result.data === null) return { state: "loading" };
  return { state: "ready", data: result.data };
}

/** A sentence for an HTTP failure that says what to do, instead of a bare status. */
export function httpFailure(what: string, status: number): string {
  if (status === 401 || status === 403) return `${what} could not be loaded: sign in again (with MFA) and retry.`;
  return `${what} could not be loaded (HTTP ${status}). Try again in a minute; if it keeps failing, check the backend log.`;
}

export function networkFailure(what: string, e: unknown): string {
  return `${what} could not be loaded: the backend is unreachable (${e instanceof Error ? e.message : String(e)}). Check your connection, then try again.`;
}

/** Turns an openapi-fetch result into an outcome. A response with no data is a failure, never an empty answer. */
export function outcomeOf<T>(what: string, r: { data?: T; response: { status: number } }): Outcome<T> {
  return r.data !== undefined && r.data !== null ? { data: r.data } : { error: httpFailure(what, r.response.status) };
}
