"use client";

import { ReviewShell } from "@/components/reports/review-shell";

import { MonthlyReviewDetail } from "./[id]/monthly-review-view";

/** /reviews: the monthly reviews, newest first, beside the latest one, and "Run review now". */
export function ReviewsView() {
  return (
    <ReviewShell selectedId="latest">
      {(reviews) => (reviews && reviews.length > 0 ? <MonthlyReviewDetail id={reviews[0].id} /> : null)}
    </ReviewShell>
  );
}
