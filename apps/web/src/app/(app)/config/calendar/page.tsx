import type { Metadata } from "next";

import { CalendarView } from "./calendar-view";

export const metadata: Metadata = { title: "Exchange calendar" };

export default function CalendarPage() {
  return <CalendarView />;
}
