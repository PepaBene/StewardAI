import { SegmentsView } from "./segments-view";

export default async function SegmentsPage({ params }: PageProps<"/companies/[ticker]/segments">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <SegmentsView ticker={ticker} />;
}
