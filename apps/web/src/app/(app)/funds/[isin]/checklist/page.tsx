import { ChecklistTab } from "../fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]/checklist">) {
  const isin = decodeURIComponent((await params).isin);
  return <ChecklistTab isin={isin} />;
}
