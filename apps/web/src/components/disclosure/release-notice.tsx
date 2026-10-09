"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { releaseNote, releaseStatus, type ReleasePage, type ReleaseStatus } from "@/lib/disclosure/release";

/** Says, above a dataset page, that its accuracy gate is not met yet. Silent when released or when the report cannot be read. */
export function ReleaseNotice({ page }: { page: ReleasePage }) {
  const [status, setStatus] = useState<ReleaseStatus>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getBrowserApiClient().GET("/disclosures/data/coverage", { params: { query: { include_watchlist: false } } });
        if (!cancelled && r.data) setStatus(releaseStatus(r.data.release_order, page));
      } catch {
        /* the coverage page reports its own failures; this note only appears when the report says so */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page]);
  if (!status || status.released) return null;
  return (
    <p role="note" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
      <strong>{releaseNote(status)}</strong> What follows is shown for reading, not yet as verified.{" "}
      <Link href="/disclosures/adjudication" className="underline underline-offset-2">Open the accuracy gate</Link>.
    </p>
  );
}
