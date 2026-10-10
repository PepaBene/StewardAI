import { BondTab } from "../fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]/bond">) {
  const isin = decodeURIComponent((await params).isin);
  return <BondTab isin={isin} />;
}
