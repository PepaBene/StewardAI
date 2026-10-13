import type { Metadata } from "next";

import { ReviewView } from "./review-view";

export const metadata: Metadata = { title: "Portfolio review" };

export default async function PortfolioReviewPage({ params }: PageProps<"/thesis/reviews/[id]">) {
  const id = Number((await params).id);
  return <ReviewView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
