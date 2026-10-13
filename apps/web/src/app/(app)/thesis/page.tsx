import type { Metadata } from "next";

import { ThesisView } from "./thesis-view";

export const metadata: Metadata = { title: "Thesis workup" };

export default function ThesisPage() {
  return <ThesisView />;
}
