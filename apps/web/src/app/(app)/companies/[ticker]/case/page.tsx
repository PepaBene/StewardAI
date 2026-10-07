import { CaseTab } from "./case-view";

export default async function CasePage({ params }: PageProps<"/companies/[ticker]/case">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <CaseTab ticker={ticker} />;
}
