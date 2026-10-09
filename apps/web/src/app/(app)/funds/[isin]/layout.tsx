import type { Metadata } from "next";

import { FundShell } from "@/components/fund/fund-shell";

export async function generateMetadata({ params }: LayoutProps<"/funds/[isin]">): Promise<Metadata> {
  return { title: decodeURIComponent((await params).isin) };
}

export default async function FundLayout({ children, params }: LayoutProps<"/funds/[isin]">) {
  const isin = decodeURIComponent((await params).isin);
  return <FundShell isin={isin}>{children}</FundShell>;
}
