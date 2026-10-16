import type { Metadata } from "next";

import { CompanyShell } from "@/components/company/company-shell";
import { shortTicker } from "@/lib/portfolio/ticker";

// The company's name is fetched in the browser by the shell, so the title uses the short ticker
// ("AAPL"), never the raw Trading212 id ("AAPL_US_EQ").
export async function generateMetadata({ params }: LayoutProps<"/companies/[ticker]">): Promise<Metadata> {
  return { title: `${shortTicker(decodeURIComponent((await params).ticker))} · Company` };
}

export default async function CompanyLayout({ children, params }: LayoutProps<"/companies/[ticker]">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <CompanyShell ticker={ticker}>{children}</CompanyShell>;
}
