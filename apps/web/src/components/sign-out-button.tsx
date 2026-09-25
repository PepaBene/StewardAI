"use client";

import { LogOutIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LOGIN_PATH } from "@/lib/auth/access";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export function SignOutButton({
  variant = "ghost",
}: {
  variant?: "ghost" | "outline" | "link";
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

  return (
    <Button variant={variant} size="sm" onClick={signOut} disabled={pending}>
      <LogOutIcon aria-hidden="true" />
      {pending ? "Signing out..." : "Sign out"}
    </Button>
  );
}
