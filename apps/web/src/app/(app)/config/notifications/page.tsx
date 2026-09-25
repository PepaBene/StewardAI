import type { Metadata } from "next";

import { NotificationsEditor } from "./notifications-editor";

export const metadata: Metadata = { title: "Notifications" };

export default function NotificationsPage() {
  return <NotificationsEditor />;
}
