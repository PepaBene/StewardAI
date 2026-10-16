import type { Metadata } from "next";
import { Suspense } from "react";

import { QueueView } from "./queue-view";

export const metadata: Metadata = { title: "Inbox" };

export default function InboxPage() {
  return (
    <Suspense>
      <QueueView />
    </Suspense>
  );
}
