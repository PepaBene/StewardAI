import type { FeedbackTargetKind } from "@/lib/api/client";

/*
 * Checklist of every screen that shows a fact, verdict, alert, news item, story or report
 * sentence, and so must carry the wrong / irrelevant / useful control (Phase 4 AC: "every
 * one of those surfaces has the controls"). `surfaces.test.ts` reads each file and fails
 * when one no longer renders a FeedbackControl for each kind listed; the README repeats the
 * list. Add a row here when a new surface shows one of these items.
 */

export type FeedbackSurface = {
  surface: string;
  route: string;
  /** Source file (relative to apps/web) that renders the controls. */
  file: string;
  kinds: readonly FeedbackTargetKind[];
};

export const FEEDBACK_SURFACES: readonly FeedbackSurface[] = [
  {
    surface: "Holding page: extracted figures and the figures behind each verdict",
    route: "/holdings/[ticker]",
    file: "src/app/(app)/holdings/[ticker]/holding-view.tsx",
    kinds: ["fact", "verdict"],
  },
  {
    surface: "Holding page: news section (one row per story)",
    route: "/holdings/[ticker]",
    file: "src/components/news/holding-news.tsx",
    kinds: ["story"],
  },
  { surface: "Evidence page", route: "/evidence/[factId]", file: "src/app/(app)/evidence/[factId]/evidence-view.tsx", kinds: ["fact"] },
  { surface: "Thesis board: verdict panel", route: "/theses", file: "src/app/(app)/theses/thesis-board-view.tsx", kinds: ["verdict"] },
  { surface: "Review queue: held and quarantined figures", route: "/review", file: "src/app/(app)/review/review-view.tsx", kinds: ["fact"] },
  { surface: "News feed: stories, lead items and copies", route: "/news", file: "src/app/(app)/news/news-view.tsx", kinds: ["story", "news_item"] },
  {
    surface: "Disputed stories: the story and each source item",
    route: "/news/disputed",
    file: "src/app/(app)/news/disputed/disputed-view.tsx",
    kinds: ["story", "news_item"],
  },
  {
    surface: "Digest: overview and section-summary sentences, stories and verdicts",
    route: "/reports/[id]",
    file: "src/app/(app)/reports/[id]/digest-view.tsx",
    kinds: ["report_sentence", "story", "verdict"],
  },
  {
    surface: "Impact note: each sentence",
    route: "/escalations/[id]",
    file: "src/app/(app)/escalations/[id]/escalation-view.tsx",
    kinds: ["report_sentence"],
  },
  { surface: "Activity: alerts (system, or all)", route: "/activity", file: "src/app/(app)/activity/alerts-panel.tsx", kinds: ["alert"] },
];
