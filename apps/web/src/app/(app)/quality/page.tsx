import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";

import { QualityView } from "./quality-view";

export const metadata: Metadata = { title: "Data quality" };

export default function QualityPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Data quality"
        description="What is wrong or missing in the data behind your portfolio, largest share of the portfolio first. A check that cannot run yet is listed, so a short list is not mistaken for full coverage."
      />
      <QualityView />
    </div>
  );
}
