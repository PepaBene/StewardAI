import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";

/** A KPI card: label, one headline figure, and an optional line of context. */
export function StatCard({
  label,
  value,
  sub,
  valueClassName,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClassName?: string;
}) {
  return (
    <Card className="gap-0 py-4">
      <CardContent className="space-y-1 px-4">
        <p className="text-sm text-text-muted">{label}</p>
        <p className={`num text-xl font-semibold tracking-tight ${valueClassName ?? ""}`}>{value}</p>
        {sub ? <div className="num text-xs text-text-muted">{sub}</div> : null}
      </CardContent>
    </Card>
  );
}
