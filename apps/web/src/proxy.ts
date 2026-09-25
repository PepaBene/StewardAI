import type { NextRequest } from "next/server";

import { enforceAuth } from "@/lib/supabase/proxy";

/*
 * Next.js 16 "proxy" (formerly middleware). Every page request passes through
 * here, so auth + MFA (aal2) is enforced server-side for every route, not just
 * in client code. The decision logic lives in src/lib/auth/access.ts.
 */
export async function proxy(request: NextRequest) {
  return enforceAuth(request);
}

export const config = {
  matcher: [
    // Everything except Next internals and static files.
    "/((?!_next/|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:svg|png|jpg|jpeg|gif|webp|avif|ico|txt|woff2?)$).*)",
  ],
};
