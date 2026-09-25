import type { Metadata } from "next";

import { EnrollForm } from "./enroll-form";

export const metadata: Metadata = { title: "Set up two-factor authentication" };

export default function MfaEnrollPage() {
  return <EnrollForm />;
}
