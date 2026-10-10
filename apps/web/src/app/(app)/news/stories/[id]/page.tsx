import type { Metadata } from "next";

import { StoryView } from "./story-view";

export const metadata: Metadata = { title: "Story" };

export default async function StoryPage({ params }: PageProps<"/news/stories/[id]">) {
  const id = Number((await params).id);
  return <StoryView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
