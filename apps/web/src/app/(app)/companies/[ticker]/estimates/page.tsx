import { EstimatesView } from "../evidence-views";

export default async function Page({ params }: PageProps<"/companies/[ticker]/estimates">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <EstimatesView ticker={ticker} />;
}
