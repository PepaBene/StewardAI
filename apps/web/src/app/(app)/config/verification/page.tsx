import type { Metadata } from "next";

import { VerificationEditor } from "./verification-editor";

export const metadata: Metadata = { title: "Verification" };

export default function VerificationPage() {
  return <VerificationEditor />;
}
