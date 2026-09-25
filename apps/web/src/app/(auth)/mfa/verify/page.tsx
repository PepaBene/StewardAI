import type { Metadata } from "next";

import { VerifyForm } from "./verify-form";

export const metadata: Metadata = { title: "Two-factor verification" };

export default function MfaVerifyPage() {
  return <VerifyForm />;
}
