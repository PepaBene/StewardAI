"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/**
 * The page's filter state lives in the URL: `params` to read, `replace(query)` to write (no new
 * history entry, no scroll jump), so a filtered atlas or screen can be bookmarked and shared.
 */
export function useQueryState() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const replace = useCallback(
    (query: string) => {
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname],
  );
  return { params, replace };
}
