import { ChecksView } from "./checks-view";

export default async function ChecksPage({ params }: PageProps<"/companies/[ticker]/checks">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <ChecksView ticker={ticker} />;
}
