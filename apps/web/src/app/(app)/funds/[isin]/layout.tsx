import type { Metadata } from "next";

import { FundShell } from "@/components/fund/fund-shell";
import { shortTicker } from "@/lib/portfolio/ticker";

// The fund's name is fetched in the browser by the shell; the title is the ISIN (or the short
// ticker when the page was opened with a Trading212 id), never a raw `_EQ` id.
export async function generateMetadata({ params }: LayoutProps<"/funds/[isin]">): Promise<Metadata> {
  return { title: `${shortTicker(decodeURIComponent((await params).isin))} · Fund` };
}

export default async function FundLayout({ children, params }: LayoutProps<"/funds/[isin]">) {
  const isin = decodeURIComponent((await params).isin);
  return <FundShell isin={isin}>{children}</FundShell>;
}
