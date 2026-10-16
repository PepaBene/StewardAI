import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The old frame's "+" registration marks. The rounded card (visual spec B1) dropped them, so
 * this renders nothing; it stays exported so the remaining call sites need no change.
 */
function BlueprintCorners() {
  return null;
}

/**
 * A framed section: square, hairline-bordered, transparent, with registration marks.
 * `as` picks the element (section by default).
 */
function Blueprint({
  as: Comp = "section",
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLElement> & { as?: "section" | "div" | "article" | "aside" | "li" }) {
  return (
    <Comp data-slot="blueprint" className={cn("blueprint", className)} {...props}>
      <BlueprintCorners />
      {children}
    </Comp>
  );
}

/**
 * The heading every framed panel opens with: the title, then the kicker as a quiet context line
 * under it (scope, period, counts). It never sits above the title as an eyebrow.
 */
function PanelHeading({
  kicker,
  title,
  action,
  className,
  level = 2,
}: {
  kicker?: React.ReactNode;
  title: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  level?: 2 | 3;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-x-3 gap-y-2", className)}>
      <div className="min-w-0 flex-[1_1_14rem]">
        <H className="panel-title">{title}</H>
        {kicker ? <div className="kicker mt-0.5 font-normal">{kicker}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export { Blueprint, BlueprintCorners, PanelHeading };
