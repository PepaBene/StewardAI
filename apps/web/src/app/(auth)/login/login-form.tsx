"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { decideAccess, HOME_PATH, isEmailAllowed, type AAL } from "@/lib/auth/access";
import { getAllowedEmail } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

function toAAL(level: string | null | undefined): AAL | null {
  return level === "aal1" || level === "aal2" ? level : null;
}

/** Email + password only. There is deliberately no sign-up path anywhere in the app. */
export function LoginForm({ disabled = false }: { disabled?: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");

    setPending(true);
    setError(null);
    try {
      const supabase = getSupabaseBrowserClient();
      const { data, error: signInError } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      if (signInError || !data.user) {
        setError(signInError?.message ?? "Sign-in failed.");
        return;
      }

      const allowedEmail = getAllowedEmail();
      if (!isEmailAllowed(data.user.email, allowedEmail)) {
        await supabase.auth.signOut();
        setError("This account is not permitted to use this StewardAI instance.");
        return;
      }

      // Password is only the first factor: route to MFA enrolment or verification.
      const { data: aal } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
      const decision = decideAccess({
        user: data.user,
        currentLevel: toAAL(aal?.currentLevel),
        nextLevel: toAAL(aal?.nextLevel),
        pathname: HOME_PATH,
        allowedEmail,
      });
      router.replace(decision.action === "redirect" ? decision.to : HOME_PATH);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      {error ? <AlertMessage title="Sign-in failed">{error}</AlertMessage> : null}
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          required
          disabled={disabled || pending}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          disabled={disabled || pending}
        />
      </div>
      <Button type="submit" className="w-full" disabled={disabled || pending}>
        {pending ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
