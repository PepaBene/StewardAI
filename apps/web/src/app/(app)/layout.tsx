import { SettingsIcon } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { AccountMenu } from "@/components/account-menu";
import { AppNav } from "@/components/app-nav";
import { AreaTabs } from "@/components/area-tabs";
import { BottomBar } from "@/components/bottom-bar";
import { ChatPanel } from "@/components/chat/chat-panel";
import { SaveReasonPrompt } from "@/components/config/save-reason-prompt";
import { InboxBell } from "@/components/inbox-bell";
import { MarketStrip } from "@/components/market-strip";
import { CommandPalette } from "@/components/command-palette";
import { PortfolioOnly } from "@/components/portfolio-only";
import { UsageBeacon } from "@/components/release/usage-beacon";
import { SyncErrorBanner } from "@/components/sync/sync-error-banner";
import { SyncIndicator } from "@/components/sync/sync-indicator";
import { SyncStatusProvider } from "@/components/sync/sync-status-provider";
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
    <UsageBeacon />
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface-raised focus:px-3 focus:py-2 focus:text-text"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-border-soft bg-page pt-[env(safe-area-inset-top,0px)]">
        <div className="mx-auto flex h-14 max-w-[1280px] items-center gap-6 px-4 md:px-6 lg:px-8">
          <Link href="/" aria-label="StewardAI, portfolio overview" className="flex shrink-0 items-center gap-2">
            <span aria-hidden="true" className="relative size-6 rounded-md bg-accent">
              <span className="absolute inset-[6px] rounded-full border-2 border-accent-foreground" />
            </span>
            <span className="hidden font-heading text-[17px] font-semibold sm:inline">StewardAI</span>
          </Link>
          <AppNav />
          <div className="ml-auto flex min-w-0 items-center gap-1 sm:gap-2">
            <CommandPalette />
            <SyncIndicator />
            <ChatPanel />
            <SaveReasonPrompt />
            <InboxBell />
            <Button asChild variant="ghost" size="icon" className="hidden text-text-muted lg:inline-flex">
              <Link href="/config/strategy" aria-label="Settings" title="Settings">
                <SettingsIcon aria-hidden="true" />
              </Link>
            </Button>
            <AccountMenu email={state.user?.email ?? null} />
          </div>
        </div>
      </header>
      <div className="pt-2">
        <AreaTabs />
      </div>
      <PortfolioOnly>
        <div className="mx-auto max-w-[1280px] px-4 md:px-6 lg:px-8">
          <MarketStrip />
        </div>
      </PortfolioOnly>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-[1280px] flex-1 px-4 pt-6 pb-24 outline-none md:px-6 lg:px-8 lg:pb-12"
      >
        <SyncErrorBanner />
        {children}
      </main>
      <BottomBar />
    </div>
    </SyncStatusProvider>
  );
}
