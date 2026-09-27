import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { ActionOut, ChatSentence, MessageOut, Source } from "@/lib/api/client";
import { reportHitHref } from "@/lib/reports/search";

import {
  actionLabel,
  actionsFor,
  chatHref,
  citedSources,
  JOB_CHECK_AFTER_MS,
  orphanActions,
  pendingMessage,
  POLL_LIMIT_MS,
  pollDecision,
  sentenceView,
  sourceNumbering,
  validateMessage,
} from "./chat";

const APP = join(__dirname, "..", "..", "app");

/** The page file a path renders in the App Router (route groups and [dynamic] segments), or null. */
function pageFor(path: string): string | null {
  const segments = path.split(/[?#]/)[0].split("/").filter(Boolean);
  const walk = (dir: string, rest: string[]): string | null => {
    const entries = readdirSync(dir).filter((e) => statSync(join(dir, e)).isDirectory());
    if (rest.length === 0) {
      if (existsSync(join(dir, "page.tsx"))) return join(dir, "page.tsx");
    } else {
      const [head, ...tail] = rest;
      if (entries.includes(head)) {
        const found = walk(join(dir, head), tail);
        if (found) return found;
      }
      for (const e of entries.filter((e) => /^\[[^.]+\]$/.test(e))) {
        const found = walk(join(dir, e), tail);
        if (found) return found;
      }
    }
    // Route groups do not add a segment.
    for (const e of entries.filter((e) => /^\(.+\)$/.test(e))) {
      const found = walk(join(dir, e), rest);
      if (found) return found;
    }
    return null;
  };
  return walk(APP, segments);
}

const source = (id: string, over: Partial<Source>): Source => ({ id, kind: "fact", ref: id, title: id, ...over });

const SOURCES: Source[] = [
  source("S1", { kind: "fact", app_path: "/evidence/12", url: "https://www.sec.gov/a" }),
  source("S2", { kind: "position", app_path: "/holdings/AAPL_US_EQ" }),
  source("S3", { kind: "report", app_path: "/reports/7" }),
  source("S4", { kind: "report", app_path: "/research/8" }),
  source("S5", { kind: "report", app_path: "/reviews/9" }),
  source("S6", { kind: "report", app_path: "/escalations/10" }),
  source("S7", { kind: "position", app_path: "/" }),
  source("S8", { kind: "news_item", url: "https://www.reuters.com/x", tier: 1 }),
  source("S9", { kind: "strategy", app_path: "/config/strategy" }),
  source("S10", { kind: "price", app_path: "/transactions" }),
];

const message = (over: Partial<MessageOut>): MessageOut => ({
  id: 1,
  role: "user",
  content: "Why did AAPL drop?",
  answer: null,
  status: "done",
  job_id: null,
  created_at: "2026-09-26T10:00:00Z",
  ...over,
});

describe("chat: source links", () => {
  it("resolves every internal source link to an existing app route (the same pages as the rest of the app)", () => {
    const numbering = sourceNumbering(SOURCES);
    const cited = citedSources(
      SOURCES.map((s) => s.id),
      numbering,
    );
    const internal = cited.filter((c) => !c.external);
    expect(internal.length).toBe(9);
    for (const c of internal) {
      expect(c.href, c.id).not.toBeNull();
      expect(pageFor(c.href!), `${c.href} has a page`).not.toBeNull();
    }
    // The paths the backend writes (research gather, review, chat tools).
    for (const path of ["/evidence/1", "/holdings/X", "/reports/1", "/research/1", "/reviews/1", "/escalations/1", "/", "/transactions", "/config/strategy"]) {
      expect(pageFor(path), path).not.toBeNull();
    }
    // Report search hits open the same pages.
    for (const type of ["digest", "research", "review", "escalation"] as const) {
      expect(pageFor(reportHitHref({ id: 1, type })), type).not.toBeNull();
    }
    expect(pageFor("/no-such-page/1")).toBeNull();
  });

  it("uses the app page first and the source's own URL otherwise", () => {
    const numbering = sourceNumbering(SOURCES);
    const [fact, news, missing] = citedSources(["S1", "S8", "S99"], numbering);
    expect(fact).toMatchObject({ number: 1, href: "/evidence/12", external: false, links: { internal: "/evidence/12", external: "https://www.sec.gov/a" } });
    expect(news).toMatchObject({ number: 8, href: "https://www.reuters.com/x", external: true });
    expect(missing).toMatchObject({ number: null, source: null, href: null });
  });
});

describe("chat: sentences", () => {
  const numbering = sourceNumbering(SOURCES);
  const sentence = (over: Partial<ChatSentence>): ChatSentence => ({ text: "x", source_ids: [], no_source: false, ...over });

  it("shows citations, 'no source' explicitly, or the unsupported reason", () => {
    expect(sentenceView(sentence({ source_ids: ["S1", "S1", "S2"] }), numbering)).toMatchObject({
      kind: "cited",
      sources: [{ id: "S1" }, { id: "S2" }],
    });
    expect(sentenceView(sentence({ no_source: true }), numbering)).toEqual({ kind: "no_source" });
    expect(sentenceView(sentence({ source_ids: [] }), numbering)).toEqual({ kind: "no_source" });
    expect(sentenceView(sentence({ source_ids: ["S1"], unsupported: " 94.1 bn is not in S1 " }), numbering)).toMatchObject({
      kind: "unsupported",
      reason: "94.1 bn is not in S1",
    });
  });
});

describe("chat: polling", () => {
  const pending = message({ id: 2, status: "pending", job_id: 50 });

  it("polls every 2 s while a message is pending, and stops when it is answered", () => {
    expect(pendingMessage([message({}), pending])).toBe(pending);
    expect(pollDecision({ messages: [message({})] }, { waitingSince: 0, now: 1000 })).toEqual({ kind: "idle" });
    expect(pollDecision({ messages: [pending] }, { waitingSince: 0, now: 1000 })).toEqual({ kind: "poll", delayMs: 2000, checkJob: false });
    expect(pollDecision(null, { waitingSince: null, now: 0 })).toEqual({ kind: "idle" });
  });

  it("checks the answer job after a while and stops when it failed or took too long", () => {
    expect(pollDecision({ messages: [pending] }, { waitingSince: 0, now: JOB_CHECK_AFTER_MS })).toMatchObject({ kind: "poll", checkJob: true });
    expect(pollDecision({ messages: [pending] }, { waitingSince: 0, now: 5000, jobStatus: "failed", jobError: "boom" })).toEqual({
      kind: "failed",
      jobId: 50,
      reason: "boom",
    });
    expect(pollDecision({ messages: [pending] }, { waitingSince: 0, now: POLL_LIMIT_MS + 1 })).toEqual({ kind: "timed-out", jobId: 50 });
  });
});

describe("chat: actions and messages", () => {
  const action = (over: Partial<ActionOut>): ActionOut => ({
    id: 1,
    message_id: 3,
    kind: "research",
    params: { ticker: "AAPL_US_EQ", profile: "quick" },
    reason: null,
    status: "proposed",
    job_id: null,
    created_at: "2026-09-26T10:00:00Z",
    decided_at: null,
    ...over,
  });

  it("attaches proposed actions to their message and keeps unattached ones", () => {
    const reply = message({
      id: 3,
      role: "assistant",
      answer: { sentences: [], sources: [], tools_used: [], actions: [2], model: null, shape: "cited" },
    });
    const actions = [action({}), action({ id: 2, message_id: null }), action({ id: 4, message_id: 99 })];
    expect(actionsFor(reply, actions).map((a) => a.id)).toEqual([1, 2]);
    expect(orphanActions([reply], actions).map((a) => a.id)).toEqual([4]);
  });

  it("words each action", () => {
    expect(actionLabel(action({}))).toBe("Research AAPL_US_EQ (profile quick)");
    expect(actionLabel(action({ kind: "earnings_check", params: { ticker: "MSFT_US_EQ" } }))).toBe("Run an earnings check for MSFT_US_EQ");
    expect(actionLabel(action({ kind: "portfolio_sync", params: {} }))).toBe("Sync the portfolio from Trading212");
  });

  it("checks a message before sending and links a conversation", () => {
    expect(validateMessage("  ")).toBe("Type a question.");
    expect(validateMessage("x".repeat(100_001))).toMatch(/At most 100\S*000/);
    expect(validateMessage("Why?")).toBeNull();
    expect(chatHref(12)).toBe("/chat?c=12");
    expect(chatHref()).toBe("/chat");
  });
});
