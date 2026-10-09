"use client";

import { Download } from "lucide-react";
import { useState } from "react";

type Fetched = { data?: unknown; error?: unknown };

/**
 * A button that fetches one CSV table from the API and saves it as a file. The table is the one
 * on the page, built server-side from the same report, so the file never disagrees with the screen.
 */
export function CsvDownload({
  label,
  filename,
  fetchCsv,
}: {
  label: string;
  filename: string;
  fetchCsv: () => Promise<Fetched>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const { data, error: failed } = await fetchCsv();
      if (failed || typeof data !== "string") {
        setError("The file could not be created. Try again in a moment.");
        return;
      }
      const url = URL.createObjectURL(new Blob([data], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("The file could not be created. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={download}
        disabled={busy}
        className="inline-flex items-center gap-1.5 border border-border px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
      >
        <Download aria-hidden="true" className="size-4" />
        {busy ? "Preparing…" : label}
      </button>
      {error ? (
        <span role="alert" className="text-xs text-loss">
          {error}
        </span>
      ) : null}
    </span>
  );
}
