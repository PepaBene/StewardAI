import { GovernanceView } from "../evidence-views";

export default async function Page({ params }: PageProps<"/companies/[ticker]/governance">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <GovernanceView ticker={ticker} />;
}
