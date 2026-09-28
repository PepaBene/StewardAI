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

type Enrollment = { factorId: string; qrCode: string; secret: string };

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

/**
 * TOTP enrolment. MFA is mandatory: the proxy sends every aal1 session without a
 * verified factor here, and nothing else is reachable until this completes.
 */
export function EnrollForm() {
  const router = useRouter();
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const supabase = getSupabaseBrowserClient();
      // Clear abandoned, never-verified attempts so enrolment can be restarted.
      const { data: factors, error: listError } = await supabase.auth.mfa.listFactors();
      if (listError) throw listError;
      for (const factor of factors.all) {
        if (factor.factor_type === "totp" && factor.status === "unverified") {
          const { error: unenrollError } = await supabase.auth.mfa.unenroll({
            factorId: factor.id,
          });
          if (unenrollError) throw unenrollError;
        }
      }

      const { data, error: enrollError } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        friendlyName: "StewardAI",
      });
      if (enrollError) throw enrollError;
      setEnrollment({ factorId: data.id, qrCode: data.totp.qr_code, secret: data.totp.secret });
    } catch (err) {
      setError(message(err));
    } finally {
      setPending(false);
    }
  }

  async function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enrollment) return;
    const code = readTotpCode(event.currentTarget);

    setPending(true);
    setError(null);
    try {
      const supabase = getSupabaseBrowserClient();
      const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({
        factorId: enrollment.factorId,
      });
      if (challengeError) throw challengeError;
      const { error: verifyError } = await supabase.auth.mfa.verify({
        factorId: enrollment.factorId,
        challengeId: challenge.id,
        code,
      });
      if (verifyError) throw verifyError;

      router.replace(HOME_PATH);
      router.refresh();
    } catch (err) {
      setError(message(err));
      setPending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[28px]">Set up two-factor authentication</CardTitle>
        <CardDescription>
          StewardAI requires an authenticator app (TOTP) for every sign-in.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? <AlertMessage title="Could not complete setup">{error}</AlertMessage> : null}

        {!enrollment ? (
          <Button onClick={start} disabled={pending} className="w-full">
            {pending ? "Preparing..." : "Set up authenticator app"}
          </Button>
        ) : (
          <form onSubmit={verify} className="space-y-4">
            <ol className="list-decimal space-y-2 pl-5 text-sm text-text-muted">
              <li>Scan this QR code with your authenticator app.</li>
              <li>Enter the 6-digit code it shows.</li>
            </ol>
            {/* Supabase returns the QR code as an SVG data URI; it needs a light
                backing to stay scannable in the dark theme. */}
            <div className="mx-auto w-fit rounded-md border border-border bg-surface-raised p-3 dark:bg-text">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={enrollment.qrCode}
                alt="QR code for adding StewardAI to your authenticator app"
                width={180}
                height={180}
              />
            </div>
            <div className="space-y-1 text-sm">
              <p className="text-text-muted">Can&apos;t scan? Enter this key manually:</p>
              <code className="block break-all rounded-md bg-surface-muted px-2 py-1 font-mono">
                {enrollment.secret}
              </code>
            </div>
            <TotpCodeField disabled={pending} />
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? "Verifying..." : "Verify and continue"}
            </Button>
          </form>
        )}
      </CardContent>
      <CardFooter className="justify-center">
        <SignOutButton variant="link" />
      </CardFooter>
    </Card>
  );
}
