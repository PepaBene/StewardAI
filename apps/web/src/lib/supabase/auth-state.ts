import type { SupabaseClient, User } from "@supabase/supabase-js";

import type { AAL } from "@/lib/auth/access";

export type AuthState = {
  user: User | null;
  currentLevel: AAL | null;
  nextLevel: AAL | null;
};

function toAAL(level: string | null | undefined): AAL | null {
  return level === "aal1" || level === "aal2" ? level : null;
}

/**
 * Reads the verified user and their MFA assurance levels.
 *
 * `getUser()` validates the access token with Supabase Auth (never trust
 * `getSession()` alone on the server). `currentLevel` is the `aal` claim of that
 * validated token. `nextLevel` is derived from the freshly fetched user's
 * verified factors as well, so a stale cookie cannot hide an enrolled factor.
 */
export async function getAuthState(supabase: SupabaseClient): Promise<AuthState> {
  const {
    data: { user },
    error,
  } = await supabase.auth.getUser();
  if (error || !user) return { user: null, currentLevel: null, nextLevel: null };

  const { data: aal } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  const hasVerifiedFactor = (user.factors ?? []).some((f) => f.status === "verified");

  return {
    user,
    currentLevel: toAAL(aal?.currentLevel),
    nextLevel: hasVerifiedFactor ? "aal2" : toAAL(aal?.nextLevel),
  };
}
