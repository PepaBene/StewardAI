/*
 * Public runtime configuration. `NEXT_PUBLIC_*` values are inlined by Next.js at
 * build time, so every access below must be a literal `process.env.NAME`.
 *
 * Nothing here throws at import time: the app builds without real values and
 * reports missing configuration clearly when it is actually used.
 */

export type SupabaseConfig = { url: string; key: string };

export const SUPABASE_ENV_HELP =
  "Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY (or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) in the repository-root .env (see .env.example) and restart the dev server / rebuild.";

/** Returns the Supabase URL and anon/publishable key, or null if not configured. */
export function getSupabaseConfig(): SupabaseConfig | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const key = (
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  )?.trim();
  if (!url || !key) return null;
  return { url, key };
}

/** Like getSupabaseConfig, but throws a descriptive error when missing. */
export function requireSupabaseConfig(): SupabaseConfig {
  const config = getSupabaseConfig();
  if (!config) {
    throw new Error(`Supabase is not configured. ${SUPABASE_ENV_HELP}`);
  }
  return config;
}

export const DEFAULT_API_BASE_URL = "http://localhost:8000";

/** Base URL of the StewardAI Python API (FastAPI). */
export function getApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
  return raw.replace(/\/+$/, "");
}

/** Optional single-allowed-user guard (defence in depth; Supabase sign-ups are disabled anyway). */
export function getAllowedEmail(): string | null {
  return process.env.NEXT_PUBLIC_ALLOWED_EMAIL?.trim() || null;
}
