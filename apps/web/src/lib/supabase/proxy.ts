import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { decideAccess, LOGIN_PATH } from "@/lib/auth/access";
import { getAllowedEmail, getSupabaseConfig } from "@/lib/env";
import { getAuthState } from "@/lib/supabase/auth-state";

/**
 * Runs on every matched request (see src/proxy.ts): refreshes the Supabase
 * session cookies and enforces "signed in + aal2" for every app route.
 */
export async function enforceAuth(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;
  const config = getSupabaseConfig();

  if (!config) {
    // Fail closed: without Supabase nothing is reachable except /login, which
    // explains what is missing.
    if (pathname === LOGIN_PATH) return NextResponse.next({ request });
    return NextResponse.redirect(new URL(LOGIN_PATH, request.url));
  }

  let response = NextResponse.next({ request });
  const cacheHeaders: Record<string, string> = {};

  const supabase = createServerClient(config.url, config.key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet, headers) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
        Object.entries(headers ?? {}).forEach(([key, value]) => {
          cacheHeaders[key] = value;
          response.headers.set(key, value);
        });
      },
    },
  });

  // Do not run code between createServerClient and getAuthState: the token
  // refresh inside getUser() must be the first thing that touches the session.
  const state = await getAuthState(supabase);
  const decision = decideAccess({
    ...state,
    pathname,
    allowedEmail: getAllowedEmail(),
  });

  if (decision.action === "allow") return response;

  if (decision.signOut) {
    await supabase.auth.signOut();
  }

  const redirect = NextResponse.redirect(new URL(decision.to, request.url));
  // Carry refreshed / cleared auth cookies and no-cache headers over.
  response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
  Object.entries(cacheHeaders).forEach(([key, value]) => redirect.headers.set(key, value));
  return redirect;
}
