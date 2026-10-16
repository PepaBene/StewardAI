"use client";

import { ActivityIcon, BookOpenIcon, ContrastIcon, LogOutIcon, MoonIcon, SunIcon, UserIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LOGIN_PATH } from "@/lib/auth/access";
import { useColorMode } from "@/lib/color-mode";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/**
 * The avatar menu at the right of the top bar: who is signed in, theme, colourblind
 * patterns, help (Glossary, Methods), API status and sign out.
 */
export function AccountMenu({ email }: { email: string | null }) {
  const { theme, setTheme } = useTheme();
  const [cvd, setCvd] = useColorMode();
  const router = useRouter();

  async function signOut() {
    try {
      await getSupabaseBrowserClient().auth.signOut();
    } finally {
      router.replace(LOGIN_PATH);
      router.refresh();
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Account and display" className="rounded-full text-text-muted">
          <span className="grid size-7 place-items-center rounded-full bg-surface-raised text-[12px] font-semibold text-text">
            {email ? email[0].toUpperCase() : <UserIcon aria-hidden="true" className="size-4" />}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        {email ? <DropdownMenuLabel className="truncate text-[13px] font-normal text-text-muted">{email}</DropdownMenuLabel> : null}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="kicker">Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="dark">
            <MoonIcon aria-hidden="true" /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">
            <SunIcon aria-hidden="true" /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">Match my device</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuCheckboxItem checked={cvd} onCheckedChange={(v) => setCvd(v === true)}>
          <ContrastIcon aria-hidden="true" /> Colourblind patterns
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/glossary">
            <BookOpenIcon aria-hidden="true" /> Glossary
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/methods">
            <BookOpenIcon aria-hidden="true" /> How figures are computed
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/status">
            <ActivityIcon aria-hidden="true" /> API status
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOutIcon aria-hidden="true" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
