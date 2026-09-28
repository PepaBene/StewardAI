import { ActivityIcon } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { AppNav } from "@/components/app-nav";
import { ChatPanel } from "@/components/chat/chat-panel";
import { MarketStrip } from "@/components/market-strip";
import { ColorModeToggle } from "@/components/color-mode-toggle";
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
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex h-[60px] max-w-[1440px] items-center gap-3 px-4 md:px-8">
          <Link href="/" aria-label="StewardAI" className="flex shrink-0 items-center gap-2.5">
            <span
              aria-hidden="true"
              className="grid size-[26px] place-items-center bg-accent font-heading text-lg leading-none font-bold text-accent-foreground"
            >
              S
            </span>
            <span className="hidden font-heading text-[22px] font-semibold tracking-[-0.01em] sm:inline">StewardAI</span>
          </Link>
          <div className="ml-2 lg:ml-7">
            <CommandPalette />
          </div>
          <div className="ml-auto flex min-w-0 items-center gap-1 whitespace-nowrap sm:gap-2">
            <SyncIndicator />
            <ChatPanel />
            <span aria-hidden="true" className="mx-1.5 hidden h-[22px] w-px bg-border lg:block" />
            {state.user?.email ? (
              <span className="hidden truncate text-[13px] text-text-muted xl:inline">
                {state.user.email}
              </span>
            ) : null}
            <div className="flex items-center text-text-muted">
              <Button asChild variant="ghost" size="icon" className="text-text-muted">
                <Link href="/status" aria-label="API status" title="API status">
                  <ActivityIcon aria-hidden="true" />
                </Link>
              </Button>
              <ColorModeToggle />
              <ThemeToggle />
              <SignOutButton iconOnly />
            </div>
          </div>
        </div>
        <div className="mx-auto flex max-w-[1440px] items-stretch gap-6 px-4 md:px-8">
          <AppNav />
          <MarketStrip />
        </div>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-[1440px] flex-1 px-4 pt-7 pb-11 outline-none md:px-8"
      >
        <SyncErrorBanner />
        {children}
      </main>
    </div>
    </SyncStatusProvider>
  );
}
