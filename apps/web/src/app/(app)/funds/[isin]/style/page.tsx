import { StyleTab } from "../fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]/style">) {
  const isin = decodeURIComponent((await params).isin);
  return <StyleTab isin={isin} />;
}
