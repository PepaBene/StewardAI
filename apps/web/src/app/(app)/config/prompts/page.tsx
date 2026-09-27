import type { Metadata } from "next";

import { PromptsEditor } from "./prompts-editor";

export const metadata: Metadata = { title: "Prompts" };

export default function PromptsPage() {
  return <PromptsEditor />;
}
