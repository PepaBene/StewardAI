import { NetworkView } from "../evidence-views";

export default async function Page({ params }: PageProps<"/companies/[ticker]/network">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <NetworkView ticker={ticker} />;
}
