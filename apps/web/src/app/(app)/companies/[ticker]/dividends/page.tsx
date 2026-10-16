import { CompanyDividendsView } from "./dividends-view";

export default async function DividendsPage({ params }: PageProps<"/companies/[ticker]/dividends">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <CompanyDividendsView ticker={ticker} />;
}
