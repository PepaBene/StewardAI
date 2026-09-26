import { describe, expect, it } from "vitest";

import type { ReplayPreview } from "@/lib/api/client";

import { changedFields, changeEffect, changeRows, channelsText, eventText, previewSummary, severityText } from "./replay";

const L = "en-US";

const preview: ReplayPreview = {
  domain: "event_rules",
  window_start: "2026-09-18T12:00:00Z",
  window_end: "2026-09-25T12:00:00Z",
  events: 42,
  alerts_before: 5,
  alerts_after: 7,
  elapsed_ms: 120,
  changes: [
    {
      key: "story:11",
      kind: "news",
      at: "2026-09-20T08:00:00Z",
      cluster_id: 11,
      ticker: "AAPL_US_EQ",
      event_type: "guidance_change",
      headline: "Apple cuts outlook",
      before: { severity: 2, decision: "digest only", channels: [], held_until: null, suppressed: null },
      after: { severity: 3, decision: "instant alert", channels: ["telegram"], held_until: null, suppressed: null },
    },
    {
      key: "story:12",
      kind: "news",
      at: "2026-09-24T23:00:00Z",
      cluster_id: 12,
      ticker: "CEZ_CZ_EQ",
      event_type: "dividend_cut",
      headline: "CEZ trims dividend",
      before: { severity: 3, decision: "instant alert", channels: ["telegram"], held_until: null, suppressed: null },
      after: { severity: 3, decision: "held for quiet hours", channels: ["telegram", "email"], held_until: "2026-09-25T05:00:00Z", suppressed: null },
    },
    {
      key: "alert:system:4",
      kind: "system",
      at: "2026-09-22T10:00:00Z",
      cluster_id: null,
      ticker: null,
      event_type: "missed_run",
      headline: "news_ingest missed its 10:00 run",
      before: { severity: 2, decision: "instant alert", channels: ["email"], held_until: null, suppressed: null },
      after: { severity: 2, decision: "suppressed", channels: ["email"], held_until: null, suppressed: "daily cap reached" },
    },
  ],
};

describe("replay preview", () => {
  it("summarises the replay: events, alerts before → after and how many change", () => {
    expect(previewSummary(preview, L)).toMatch(/^Replayed 42 events from .+ to .+: 5 alerts → 7 alerts \(\+2\); 3 events change\.$/);
    expect(previewSummary({ ...preview, alerts_after: 5, changes: [] }, L)).toMatch(/5 alerts → 5 alerts \(no change in count\); no event's outcome changes\.$/);
    expect(previewSummary({ ...preview, events: 1, alerts_before: 1, alerts_after: 0, changes: [preview.changes[0]] }, L)).toMatch(
      /Replayed 1 event .*: 1 alert → 0 alerts \(−1\); 1 event changes\.$/,
    );
  });

  it("names what changed for each event", () => {
    const [raised, held, capped] = preview.changes;
    expect(changedFields(raised)).toEqual(["severity", "channels", "decision"]);
    expect(changeEffect(raised)).toEqual({ kind: "new_alert", label: "Would now alert" });
    expect(changedFields(held)).toEqual(["channels", "held_until", "decision"]);
    expect(changeEffect(held).kind).toBe("changed");
    expect(changedFields(capped)).toEqual(["suppressed", "decision"]);
    expect(changeEffect(capped)).toEqual({ kind: "no_longer_alerts", label: "Would no longer alert" });
    // Channel order does not matter.
    expect(changedFields({ before: { ...held.after, channels: ["email", "telegram"] }, after: held.after })).toEqual([]);
  });

  it("lists the changes newest first with before and after in words", () => {
    const rows = changeRows(preview, L);
    expect(rows.map((r) => r.key)).toEqual(["story:12", "alert:system:4", "story:11"]);
    const [held, capped, raised] = rows;
    expect(raised).toMatchObject({ headline: "Apple cuts outlook", ticker: "AAPL_US_EQ", event: "Guidance change" });
    expect(raised.before).toEqual({ severity: "2 · Highlight", decision: "digest only", channels: "no channel", heldUntil: null, suppressed: null });
    expect(raised.after).toMatchObject({ severity: "3 · Alert", channels: "Telegram" });
    expect(held.after.channels).toBe("Telegram, Email");
    expect(held.after.heldUntil).toMatch(/2026/);
    expect(capped).toMatchObject({ event: "Monitoring · Missed run" });
    expect(capped.after.suppressed).toBe("daily cap reached");
  });

  it("formats severities, channels and events", () => {
    expect(severityText(0)).toBe("0 · Ignored");
    expect(severityText(5)).toBe("5 · Alert");
    expect(channelsText(["telegram", "sms"])).toBe("Telegram, sms");
    expect(eventText({ key: "alert:thesis_breach:3", kind: "thesis_breach", event_type: null })).toBe("Thesis breach");
    expect(eventText({ key: "alert:portfolio:3", kind: "portfolio", event_type: "portfolio" })).toBe("Portfolio");
  });
});
