import { FinancialsView } from "./financials-view";

export default async function FinancialsPage({ params }: PageProps<"/companies/[ticker]/financials">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <FinancialsView ticker={ticker} />;
}
