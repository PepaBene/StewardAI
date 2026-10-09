import type { Metadata } from "next";

import { CompanyShell } from "@/components/company/company-shell";

export async function generateMetadata({ params }: LayoutProps<"/companies/[ticker]">): Promise<Metadata> {
  return { title: decodeURIComponent((await params).ticker) };
}

export default async function CompanyLayout({ children, params }: LayoutProps<"/companies/[ticker]">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <CompanyShell ticker={ticker}>{children}</CompanyShell>;
}
