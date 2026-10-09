import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { ReleaseTabs } from "@/components/release/release-tabs";

export const metadata: Metadata = { title: "Release checks" };

export default function ReleaseLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="Release checks"
        description="Whether each feature has earned its place in the app: free access tested, displayed facts compared with their sources, and no spend. A feature that fails stays labelled, never quietly replaced."
      />
      <ReleaseTabs />
      {children}
    </div>
  );
}
