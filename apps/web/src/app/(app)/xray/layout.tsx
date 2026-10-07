import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { XrayTabs } from "@/components/xray/xray-tabs";

export const metadata: Metadata = { title: "X-Ray" };

export default function XrayLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="X-Ray"
        description="What you really own once funds are opened up: the same holdings seen by sector, country, issuer, fee and more, with what is unknown kept visible."
      />
      <Suspense>
        <XrayTabs />
      </Suspense>
      {children}
    </div>
  );
}
