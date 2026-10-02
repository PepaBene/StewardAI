import { FlaskConicalIcon } from "lucide-react";

/**
 * Marks a screen whose numbers come from a typed fixture because its backend endpoint has
 * not landed. Fixture values are illustrative: they must never be mistaken for the owner's
 * data, so the banner sits above the page header content, not in a footnote.
 */
export function PreviewBanner({ endpoint }: { endpoint: string }) {
  return (
    <div role="note" className="flex items-start gap-2.5 border border-dashed border-info-signal/60 bg-info-signal-subtle px-3.5 py-2.5 text-sm">
      <FlaskConicalIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info-signal" />
      <p>
        <strong className="font-semibold">Preview with illustrative fixture data.</strong>{" "}
        <span className="text-text-muted">
          These figures are synthetic and typed to the contract of <code className="font-mono text-xs">{endpoint}</code>; they
          are not your holdings or real market data. The screen switches to live data when that endpoint is available.
        </span>
      </p>
    </div>
  );
}
