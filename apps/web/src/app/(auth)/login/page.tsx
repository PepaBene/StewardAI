import type { Metadata } from "next";

import { AlertMessage } from "@/components/alert-message";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getSupabaseConfig, SUPABASE_ENV_HELP } from "@/lib/env";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  not_allowed: "This account is not permitted to use this StewardAI instance. You have been signed out.",
};

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { error } = await searchParams;
  const errorMessage = typeof error === "string" ? ERRORS[error] : undefined;
  const configured = getSupabaseConfig() !== null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Sign in</CardTitle>
        <CardDescription>
          Owner access only. You will be asked for your authenticator code next.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!configured ? (
          <AlertMessage title="Supabase is not configured">{SUPABASE_ENV_HELP}</AlertMessage>
        ) : null}
        {errorMessage ? <AlertMessage title="Access denied">{errorMessage}</AlertMessage> : null}
        <LoginForm disabled={!configured} />
      </CardContent>
    </Card>
  );
}
