"use client";

import { useState } from "react";

import { AllChecksDialog, CheckList } from "@/components/insight/check-list";
import { LimitedState } from "@/components/insight/limited-state";
import { Blueprint } from "@/components/ui/blueprint";
import { AXIS_LABEL } from "@/lib/insight/insight";

import { useShellChecks } from "./company-context";
import { SectionFigures } from "./section-figures";
import type { CompanySection } from "./sections";

/**
 * How every section opens (visual spec B39): the section name and its key figures, then for the five axis
 * sections the "n of 6 checks" line with the rows of that axis, and "See all checks" for the full
 * list. Sections without an axis (Overview, Ownership, Research, Network) open with the name only.
 */
export function SectionHead({ section, ticker, name }: { section: CompanySection; ticker: string; name: string }) {
  const state = useShellChecks();
  const [open, setOpen] = useState(false);
  const checks = state.kind === "ok" ? state.data : null;
  const rows = section.axis && checks ? checks.checks.filter((c) => c.axis === section.axis) : [];

  return (
    <Blueprint aria-label={section.label} className="space-y-4 px-[22px] py-4">
      <h2 className="panel-title">{section.label}</h2>
      <SectionFigures ticker={ticker} section={section.id} />
      {section.axis ? (
        state.kind === "loading" ? (
          <p className="text-sm text-text-muted" role="status">
            Loading the {AXIS_LABEL[section.axis].toLowerCase()} checks…
          </p>
        ) : checks && rows.length > 0 ? (
          <>
            <CheckList checks={rows} ticker={ticker} limit={3} />
            <button type="button" onClick={() => setOpen(true)} className="text-sm font-medium text-accent-ink underline underline-offset-2">
              See all checks
            </button>
            <AllChecksDialog
              checks={checks.checks}
              risks={checks.risks}
              ticker={ticker}
              name={name}
              open={open}
              onClose={() => setOpen(false)}
              initialAxis={section.axis}
            />
          </>
        ) : (
          <LimitedState>The {AXIS_LABEL[section.axis].toLowerCase()} checks have not been computed for this company yet.</LimitedState>
        )
      ) : null}
    </Blueprint>
  );
}
