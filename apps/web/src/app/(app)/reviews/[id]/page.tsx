import type { Metadata } from "next";

import { MonthlyReviewView } from "./monthly-review-view";

export const metadata: Metadata = { title: "Monthly review" };

export default async function MonthlyReviewPage({ params }: PageProps<"/reviews/[id]">) {
  const id = Number((await params).id);
  return <MonthlyReviewView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
