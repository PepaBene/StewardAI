import { ReportView } from "./report-view";

export default async function ReportPage({ params }: PageProps<"/companies/[ticker]/report">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <ReportView ticker={ticker} />;
}
