import type { Metadata } from "next";

import { InvestorsView } from "./investors-view";

export const metadata: Metadata = { title: "13F investors" };

export default function InvestorsPage() { return <InvestorsView />; }
