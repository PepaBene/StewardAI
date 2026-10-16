import type { Metadata } from "next";

import { ChecksEditor } from "./checks-editor";

export const metadata: Metadata = { title: "Checks" };

export default function ChecksPage() {
  return <ChecksEditor />;
}
