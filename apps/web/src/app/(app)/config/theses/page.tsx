import type { Metadata } from "next";

import { ThesesEditor } from "./theses-editor";

export const metadata: Metadata = { title: "Theses" };

export default function ThesesPage() {
  return <ThesesEditor />;
}
