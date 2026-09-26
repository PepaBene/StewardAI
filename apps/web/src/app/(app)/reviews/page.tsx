import type { Metadata } from "next";

import { ReviewsView } from "./reviews-view";

export const metadata: Metadata = { title: "Monthly reviews" };

export default function ReviewsPage() {
  return <ReviewsView />;
}
