import { ConsensusView } from "./consensus-view";

export default async function ConsensusPage({ params }: PageProps<"/companies/[ticker]/consensus">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <ConsensusView ticker={ticker} />;
}
