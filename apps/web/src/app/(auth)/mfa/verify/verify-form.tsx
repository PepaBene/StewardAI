"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { SignOutButton } from "@/components/sign-out-button";
import { readTotpCode, TotpCodeField } from "@/components/totp-code-field";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { HOME_PATH } from "@/lib/auth/access";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

/** Second factor for a password-only (aal1) session; upgrades it to aal2. */
export function VerifyForm() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = readTotpCode(event.currentTarget);

    setPending(true);
    setError(null);
    try {
      const supabase = getSupabaseBrowserClient();
      const { data: factors, error: listError } = await supabase.auth.mfa.listFactors();
      if (listError) throw listError;
      const factor = factors.totp[0];
      if (!factor) {
        throw new Error("No verified authenticator app is enrolled for this account.");
      }

      const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({
        factorId: factor.id,
      });
      if (challengeError) throw challengeError;
      const { error: verifyError } = await supabase.auth.mfa.verify({
        factorId: factor.id,
        challengeId: challenge.id,
        code,
      });
      if (verifyError) throw verifyError;

      router.replace(HOME_PATH);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Two-factor verification</CardTitle>
        <CardDescription>Enter the code from your authenticator app.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={verify} className="space-y-4">
          {error ? <AlertMessage title="Verification failed">{error}</AlertMessage> : null}
          <TotpCodeField disabled={pending} />
          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? "Verifying..." : "Verify"}
          </Button>
        </form>
      </CardContent>
      <CardFooter className="justify-center">
        <SignOutButton variant="link" />
      </CardFooter>
    </Card>
  );
}
