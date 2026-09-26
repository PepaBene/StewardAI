import { ActivityIcon } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { AppNav } from "@/components/app-nav";
import { ChatPanel } from "@/components/chat/chat-panel";
import { CommandPalette } from "@/components/command-palette";
import { SignOutButton } from "@/components/sign-out-button";
import { SyncErrorBanner } from "@/components/sync/sync-error-banner";
import { SyncIndicator } from "@/components/sync/sync-indicator";
import { SyncStatusProvider } from "@/components/sync/sync-status-provider";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { decideAccess, HOME_PATH, LOGIN_PATH } from "@/lib/auth/access";
import { getAllowedEmail, getSupabaseConfig } from "@/lib/env";
import { getAuthState } from "@/lib/supabase/auth-state";
import { createSupabaseServerClient } from "@/lib/supabase/server";

/*
 * Authenticated shell. The proxy (src/proxy.ts) already enforces
 * "signed in + aal2" on every request; this layout re-checks it server-side as
 * defence in depth so a matcher mistake cannot expose an app page.
 */
export default async function AppLayout({ children }: LayoutProps<"/">) {
  // Always render per request (auth-dependent), even when env is missing at build time.
  await connection();
  if (!getSupabaseConfig()) redirect(LOGIN_PATH);

  const supabase = await createSupabaseServerClient();
  const state = await getAuthState(supabase);
  const decision = decideAccess({
    ...state,
    pathname: HOME_PATH, // any protected path: the rule is the same for all of them
    allowedEmail: getAllowedEmail(),
  });
  if (decision.action === "redirect") redirect(decision.to);

  return (
    <SyncStatusProvider>
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface-raised focus:px-3 focus:py-2 focus:text-text"
      >
        Skip to content
      </a>
      <header className="border-b border-border bg-surface-raised">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
          <Link href="/" className="rounded-sm text-base font-semibold tracking-tight">
            StewardAI
          </Link>
          <div className="ml-auto flex items-center gap-1">
            <CommandPalette />
            <ChatPanel />
            <SyncIndicator />
            {state.user?.email ? (
              <span className="hidden truncate text-sm text-text-muted lg:inline">
                {state.user.email}
              </span>
            ) : null}
            <Button asChild variant="ghost" size="sm">
              <Link href="/status">
                <ActivityIcon aria-hidden="true" />
                <span>API status</span>
              </Link>
            </Button>
            <ThemeToggle />
            <SignOutButton />
          </div>
        </div>
        <div className="mx-auto max-w-7xl px-4">
          <AppNav />
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 outline-none">
        <SyncErrorBanner />
        {children}
      </main>
    </div>
    </SyncStatusProvider>
  );
}
