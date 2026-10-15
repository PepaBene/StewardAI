import type { Metadata } from "next";

import { MemoView } from "./memo-view";

export const metadata: Metadata = { title: "Thesis memo" };

export default async function ThesisMemoPage({ params }: PageProps<"/thesis/memos/[id]">) {
  const id = Number((await params).id);
  return <MemoView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
