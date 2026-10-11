import type { Metadata } from "next";

import { AskView } from "./ask-view";

export const metadata: Metadata = { title: "Ask the news" };

export default function AskPage() {
  return <AskView />;
}
