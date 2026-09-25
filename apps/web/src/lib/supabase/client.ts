import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

import { requireSupabaseConfig } from "@/lib/env";

/**
 * Supabase client for Client Components. Created lazily (never at import time)
 * so pages still build without env values; throws a clear error if unset.
 */
export function getSupabaseBrowserClient(): SupabaseClient {
  const { url, key } = requireSupabaseConfig();
  // createBrowserClient is a singleton in the browser.
  return createBrowserClient(url, key);
}
