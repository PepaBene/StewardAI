import type { Metadata } from "next";

import { GlossaryView } from "./glossary-view";

export const metadata: Metadata = { title: "Glossary" };

export default function GlossaryPage() {
  return <GlossaryView />;
}
