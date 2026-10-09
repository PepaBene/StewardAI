import { PerformanceTab } from "../fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]/performance">) {
  const isin = decodeURIComponent((await params).isin);
  return <PerformanceTab isin={isin} />;
}
