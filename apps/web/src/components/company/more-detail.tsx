"use client";

import { useState, type ReactNode } from "react";

/**
 * A closed block whose content mounts (and so fetches) only when opened. Section pages use it for
 * the holding-page panels below the fold, so the first screen stays short and the requests lazy.
 */
export function MoreDetail({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const [opened, setOpened] = useState(false);
  return (
    <details
      className="blueprint rounded-card px-[22px] py-3"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) setOpened(true);
      }}
    >
      <summary className="cursor-pointer text-sm font-medium">
        {title}
        {hint ? <span className="ml-2 font-normal text-text-muted">{hint}</span> : null}
      </summary>
      {opened ? <div className="mt-3 space-y-5">{children}</div> : null}
    </details>
  );
}
