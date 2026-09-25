import type { Metadata } from "next";

import { EventRulesEditor } from "./event-rules-editor";

export const metadata: Metadata = { title: "Event rules" };

export default function EventRulesPage() {
  return <EventRulesEditor />;
}
