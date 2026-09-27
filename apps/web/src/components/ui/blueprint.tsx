import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The four "+" registration marks of a blueprint frame. Render them as children of any
 * element with the `blueprint` utility (position: relative + hairline border); they sit
 * just outside the corners and are out of flow, so they never affect layout.
 */
function BlueprintCorners() {
  return (
    <>
      <i aria-hidden="true" className="bp-corner" data-corner="tl" />
      <i aria-hidden="true" className="bp-corner" data-corner="tr" />
      <i aria-hidden="true" className="bp-corner" data-corner="bl" />
      <i aria-hidden="true" className="bp-corner" data-corner="br" />
    </>
  );
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

/** Kicker + condensed title, the heading every framed panel in the design opens with. */
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
    <div className={cn("flex items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        {kicker ? <div className="kicker">{kicker}</div> : null}
        <H className="mt-1 text-[22px] leading-tight font-semibold">{title}</H>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export { Blueprint, BlueprintCorners, PanelHeading };
