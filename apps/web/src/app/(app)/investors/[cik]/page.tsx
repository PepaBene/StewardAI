import type { Metadata } from "next";

import { InvestorView } from "./investor-view";

export const metadata: Metadata = { title: "13F manager" };

export default async function InvestorPage({ params }: { params: Promise<{ cik: string }> }) {
  const { cik } = await params;
  return <InvestorView cik={cik} />;
}
