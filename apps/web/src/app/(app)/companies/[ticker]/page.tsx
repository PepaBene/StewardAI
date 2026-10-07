import { OverviewView } from "./overview-view";

export default async function CompanyOverviewPage({ params }: PageProps<"/companies/[ticker]">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <OverviewView ticker={ticker} />;
}
