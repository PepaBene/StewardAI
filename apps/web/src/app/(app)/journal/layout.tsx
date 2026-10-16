import type { Metadata } from "next";
import type { ReactNode } from "react";

import { JournalTabs } from "@/components/journal/journal-tabs";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Journal" };

export default function JournalLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Decision journal"
        description="Write down why you decided, while you still do not know how it turned out. Entries are never edited; a later review is a new entry that points back."
      />
      <JournalTabs />
      {children}
    </div>
  );
}
