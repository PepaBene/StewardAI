import type { Metadata } from "next";

import { HoldingView } from "./holding-view";

export async function generateMetadata({ params }: PageProps<"/holdings/[ticker]">): Promise<Metadata> {
  return { title: decodeURIComponent((await params).ticker) };
}

export default async function HoldingPage({ params }: PageProps<"/holdings/[ticker]">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <HoldingView ticker={ticker} />;
}
