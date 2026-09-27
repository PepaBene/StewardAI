"use client";

import { LogOutIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth/access";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export function SignOutButton({
  variant = "ghost",
  iconOnly = false,
}: {
  variant?: "ghost" | "outline" | "link";
  /** Header use: just the icon, with the label as its accessible name. */
  iconOnly?: boolean;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    try {
      await getSupabaseBrowserClient().auth.signOut();
    } finally {
      router.replace(LOGIN_PATH);
      router.refresh();
    }
  }

  if (iconOnly) {
    return (
      <Button
        variant={variant}
        size="icon"
        className="text-text-muted"
        onClick={signOut}
        disabled={pending}
        aria-label={pending ? "Signing out" : "Sign out"}
        title="Sign out"
      >
        <LogOutIcon aria-hidden="true" />
      </Button>
    );
  }

  return (
    <Button variant={variant} size="sm" onClick={signOut} disabled={pending}>
      <LogOutIcon aria-hidden="true" />
      {pending ? "Signing out..." : "Sign out"}
    </Button>
  );
}
