import { ValuationView } from "./valuation-tabs";

export default async function ValuationPage({ params }: PageProps<"/companies/[ticker]/valuation">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <ValuationView ticker={ticker} />;
}
