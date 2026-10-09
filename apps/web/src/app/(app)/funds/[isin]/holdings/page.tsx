import { HoldingsTab } from "../fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]/holdings">) {
  const isin = decodeURIComponent((await params).isin);
  return <HoldingsTab isin={isin} />;
}
