import type { Metadata } from "next";

import { ThesisBoardView } from "./thesis-board-view";

export const metadata: Metadata = { title: "Theses" };

export default function ThesisBoardPage() {
  return <ThesisBoardView />;
}
