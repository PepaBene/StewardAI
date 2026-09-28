import type { HoldingProfile, ManualSegmentRow } from "@/lib/api/client";

import { lines } from "./theses-form";

/*
 * Editable form state for a holding profile's SEC XBRL fields: the 13F share basis
 * (`share_basis`) and revenue rows entered by hand (`manual_segments`). Kept apart from the
 * rest of the profile form; a profile body carries these keys only when something is set,
 * so profiles without them round-trip unchanged.
 */

export type SegmentAxis = ManualSegmentRow["axis"];
export const SEGMENT_AXES: readonly SegmentAxis[] = ["geographic", "segment", "product"];
export const SEGMENT_AXIS_LABELS: Record<SegmentAxis, string> = {
  geographic: "Region",
  segment: "Business segment",
  product: "Product",
};

export type ManualSegmentForm = {
  key: string;
  label: string;
  axis: SegmentAxis;
  value: string;
  currency: string;
  period_start: string;
  period_end: string;
  source: string;
};

export type FundamentalsForm = {
  /** Home-market shares per US-listed share; empty = default (TSM 5, others 1). */
  ads_ratio: string;
  /** One share-class member per line (us-gaap:CommonClassAMember); empty = all classes. */
  share_classes: string;
  manual_shares_outstanding: string;
  manual_as_of: string;
  manual_source: string;
  segments: ManualSegmentForm[];
};

let counter = 0;
const nextKey = () => `ms${++counter}`;

export function newFundamentals(): FundamentalsForm {
  return {
    ads_ratio: "",
    share_classes: "",
    manual_shares_outstanding: "",
    manual_as_of: "",
    manual_source: "",
    segments: [],
  };
}

/** A new manual row; copies currency, period and source from the previous row to save typing. */
export function newManualSegment(previous?: ManualSegmentForm): ManualSegmentForm {
  return {
    key: nextKey(),
    label: "",
    axis: previous?.axis ?? "geographic",
    value: "",
    currency: previous?.currency ?? "",
    period_start: previous?.period_start ?? "",
    period_end: previous?.period_end ?? "",
    source: previous?.source ?? "",
  };
}

const numberText = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));

export function fundamentalsFromProfile(
  profile: Pick<HoldingProfile, "share_basis" | "manual_segments">,
): FundamentalsForm {
  const basis = profile.share_basis;
  return {
    ads_ratio: numberText(basis?.ads_ratio),
    share_classes: (basis?.share_classes ?? []).join("\n"),
    manual_shares_outstanding: numberText(basis?.manual_shares_outstanding),
    manual_as_of: basis?.manual_as_of ?? "",
    manual_source: basis?.manual_source ?? "",
    segments: (profile.manual_segments ?? []).map((m) => ({
      key: nextKey(),
      label: m.label,
      axis: m.axis,
      value: String(m.value),
      currency: m.currency,
      period_start: m.period_start,
      period_end: m.period_end,
      source: m.source,
    })),
  };
}

/** "1 234,5" -> 1234.5; "" -> null. Invalid text stays a NaN the API rejects with a message. */
export function parseAmount(text: string): number | null {
  const clean = text.trim().replace(/[\s ]/g, "").replace(",", ".");
  return clean ? Number(clean) : null;
}

/** The profile keys to send: only those with something set. */
export function fundamentalsToProfile(
  form: FundamentalsForm,
): Partial<Pick<HoldingProfile, "share_basis" | "manual_segments">> {
  const out: Partial<Pick<HoldingProfile, "share_basis" | "manual_segments">> = {};
  const ratio = parseAmount(form.ads_ratio);
  const classes = lines(form.share_classes);
  const manual = parseAmount(form.manual_shares_outstanding);
  const asOf = form.manual_as_of.trim();
  const source = form.manual_source.trim();
  if (ratio !== null || classes.length > 0 || manual !== null || asOf || source) {
    out.share_basis = {
      ads_ratio: ratio,
      share_classes: classes,
      manual_shares_outstanding: manual,
      manual_as_of: asOf || null,
      manual_source: source,
    };
  }
  if (form.segments.length > 0) {
    out.manual_segments = form.segments.map((s) => ({
      label: s.label.trim(),
      axis: s.axis,
      value: parseAmount(s.value) ?? Number.NaN,
      currency: s.currency.trim().toUpperCase(),
      period_start: s.period_start.trim(),
      period_end: s.period_end.trim(),
      source: s.source.trim(),
    }));
  }
  return out;
}
