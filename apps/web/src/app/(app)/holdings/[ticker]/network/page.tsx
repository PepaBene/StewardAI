import type { Metadata } from "next";
import { Suspense } from "react";

import { NetworkView } from "./network-view";

export async function generateMetadata({ params }: { params: Promise<{ ticker: string }> }): Promise<Metadata> {
  return { title: `${decodeURIComponent((await params).ticker)} business network` };
}

export default async function NetworkPage({ params }: { params: Promise<{ ticker: string }> }) {
  const ticker = decodeURIComponent((await params).ticker);
  return (
    <Suspense>
      <NetworkView ticker={ticker} />
    </Suspense>
  );
}
