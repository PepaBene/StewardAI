import * as React from "react";

import { cn } from "@/lib/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-control border border-border bg-surface-muted px-3 py-1 text-base text-text transition-[color,box-shadow] outline-none placeholder:text-text-muted disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
        "aria-invalid:border-warning-signal aria-invalid:ring-2 aria-invalid:ring-warning-signal/30",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
