import { RatiosTab } from "./ratios-view";

export default async function RatiosPage({ params }: PageProps<"/companies/[ticker]/ratios">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <RatiosTab ticker={ticker} />;
}
