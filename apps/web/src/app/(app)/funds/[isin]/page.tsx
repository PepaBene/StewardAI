import { ProfileView } from "./fund-views";

export default async function Page({ params }: PageProps<"/funds/[isin]">) {
  const isin = decodeURIComponent((await params).isin);
  return <ProfileView isin={isin} />;
}
