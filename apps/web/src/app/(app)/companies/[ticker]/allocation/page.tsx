import { AllocationView } from "../evidence-views";

export default async function Page({ params }: PageProps<"/companies/[ticker]/allocation">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <AllocationView ticker={ticker} />;
}
