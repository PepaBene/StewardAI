import { PeersView } from "./peers-view";

export default async function PeersPage({ params }: PageProps<"/companies/[ticker]/peers">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <PeersView ticker={ticker} />;
}
