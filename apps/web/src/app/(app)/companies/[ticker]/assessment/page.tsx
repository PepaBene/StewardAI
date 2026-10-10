import { AssessmentView } from "./assessment-view";

export default async function AssessmentPage({ params }: PageProps<"/companies/[ticker]/assessment">) {
  const ticker = decodeURIComponent((await params).ticker);
  return <AssessmentView ticker={ticker} />;
}
