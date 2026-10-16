import type { ReactNode } from "react";

import { ChartFooter } from "@/components/insight/chart-footer";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { cn } from "@/lib/utils";

/*
 * ChartFrame (visual spec B1), portfolio flavour: a framed card around one chart. Title, an
 * optional one-line reading under it, range chips or toggles on the right, the plot, then the
 * provenance footer. Pass `footer` already wired to a Data toggle (ChartWithFooter) to skip the
 * built-in one.
 */
export function ChartFrame({
  title,
  kicker,
  reading,
  controls,
  children,
  dataset,
  asOf,
  source,
  learnHref,
  footer,
  className,
}: {
  title: ReactNode;
  kicker?: ReactNode;
  /** The chart's verdict sentence (a VerdictLine or plain text). */
  reading?: ReactNode;
  controls?: ReactNode;
  children: ReactNode;
  dataset?: string;
  asOf?: string | null;
  source?: string | null;
  learnHref?: string;
  /** Replaces the built-in footer. */
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <Blueprint className={cn("min-w-0 px-[22px] py-4", className)}>
      <PanelHeading kicker={kicker} title={title} action={controls} />
      {reading ? <div className="mt-1 text-sm text-text-muted">{reading}</div> : null}
      <div className="mt-3">{children}</div>
      {footer ?? (dataset ? <ChartFooter dataset={dataset} asOf={asOf} source={source} learnHref={learnHref} /> : null)}
    </Blueprint>
  );
}
