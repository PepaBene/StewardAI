import type { Metadata } from "next";
import { Suspense } from "react";

import { ReviewView } from "./review-view";

export const metadata: Metadata = { title: "Review queue" };

export default function ReviewPage() {
  return (
    <Suspense>
      <ReviewView />
    </Suspense>
  );
}
