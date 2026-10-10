import type { Metadata } from "next";

import { StoriesView } from "./stories-view";

export const metadata: Metadata = { title: "News stories" };

export default function StoriesPage() {
  return <StoriesView />;
}
