import type { Metadata } from "next";
import { Suspense } from "react";

import { NewsView } from "./news-view";

export const metadata: Metadata = { title: "News" };

export default function NewsPage() {
  return (
    <Suspense>
      <NewsView />
    </Suspense>
  );
}
