import { describe, expect, it } from "vitest";

import { budgetFromBody, budgetToBody } from "@/app/(app)/config/budget/budget-form";

import { jobsFromBody, jobsToBody } from "./jobs-form";
import {
  DEFAULT_SOURCES,
  eventRulesFromBody,
  eventRulesToBody,
  notificationsFromBody,
  notificationsToBody,
  sourcesFromBody,
  sourcesToBody,
} from "./news-forms";
import { researchProfilesFromBody, researchProfilesToBody } from "./research-profiles-form";
import { strategyFromBody, strategyToBody } from "./strategy-form";
import { verificationFromBody, verificationToBody } from "./verification-form";

/*
 * A config editor compares the body its form would send with a baseline taken when the config
 * loaded; any difference reads as "Unsaved changes". With nothing saved yet the form starts from
 * the defaults, and the baseline must be those same defaults (it used to be empty, so a freshly
 * loaded editor was dirty). The form must also reproduce its own body: reading a body into the
 * form and back, twice, must not change it, or the editor would turn dirty by itself.
 */
type Editor = { name: string; fresh: () => unknown; again: (body: never) => unknown };

const EDITORS: Editor[] = [
  { name: "event-rules", fresh: () => eventRulesToBody(eventRulesFromBody(null)), again: (b) => eventRulesToBody(eventRulesFromBody(b)) },
  { name: "notifications", fresh: () => notificationsToBody(notificationsFromBody(null)), again: (b) => notificationsToBody(notificationsFromBody(b)) },
  { name: "news-sources", fresh: () => sourcesToBody(sourcesFromBody(null, DEFAULT_SOURCES)), again: (b) => sourcesToBody(sourcesFromBody(b, DEFAULT_SOURCES)) },
  { name: "jobs", fresh: () => jobsToBody(jobsFromBody(null)), again: (b) => jobsToBody(jobsFromBody(b)) },
  { name: "verification", fresh: () => verificationToBody(verificationFromBody(null)), again: (b) => verificationToBody(verificationFromBody(b)) },
  { name: "strategy", fresh: () => strategyToBody(strategyFromBody(null)), again: (b) => strategyToBody(strategyFromBody(b)) },
  { name: "budget", fresh: () => budgetToBody(budgetFromBody(null)), again: (b) => budgetToBody(budgetFromBody(b)) },
  { name: "research-profiles", fresh: () => researchProfilesToBody(researchProfilesFromBody(null)), again: (b) => researchProfilesToBody(researchProfilesFromBody(b)) },
];

describe("freshly loaded editor is not dirty", () => {
  for (const e of EDITORS) {
    it(`${e.name}: the defaults form reproduces its own body`, () => {
      const first = e.fresh();
      const second = e.again(first as never);
      expect(JSON.stringify(second)).toBe(JSON.stringify(first));
      // Loading the saved copy of that body gives the same baseline as loading the defaults.
      expect(JSON.stringify(e.again(second as never))).toBe(JSON.stringify(first));
    });
  }
});
