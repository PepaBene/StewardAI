import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type Readiness = S["Readiness"];
export type FeatureReadiness = S["FeatureReadiness"];
export type InputState = S["InputState"];
export type AuditOverview = S["AuditOverview"];
export type AuditIn = S["AuditIn"];
export type AuditRow = S["AuditRow"];
export type AuditSummary = S["AuditSummary"];
export type TargetProgress = S["TargetProgress"];
export type PilotState = S["PilotState"];
export type PilotReport = S["PilotReport"];
export type Status = FeatureReadiness["status"];
export type SubjectKind = AuditIn["subject_kind"];
export type AuditResult = AuditIn["result"];

/** What the status means for the owner, in words; colour is never the only signal. */
export const STATUS_LABEL: Record<Status, { label: string; help: string }> = {
  ready: { label: "Ready", help: "Every input is reachable on the free tier and holds data." },
  partial: { label: "Partial", help: "Works, but part of its input is missing or limited." },
  untested: { label: "Not tested", help: "Free access has not been tested for a core input yet." },
  unavailable: { label: "Unavailable", help: "A core input has no free source or no data." },
};

export const KIND_LABEL: Record<SubjectKind, string> = {
  security: "Securities",
  etf: "Funds (ETFs)",
  corporate_action: "Corporate-action histories",
  cash_flow_window: "Cash-flow windows",
};

export const KIND_ONE: Record<SubjectKind, string> = {
  security: "Security",
  etf: "ETF",
  corporate_action: "Corporate-action history",
  cash_flow_window: "Cash-flow window",
};

/** Feature ids the release gate knows (steward/release/readiness.py FEATURES). */
export const FEATURE_CHOICES: readonly { id: string; label: string }[] = [
  { id: "discover", label: "Discover and saved screens" },
  { id: "company", label: "Company workbench" },
  { id: "funds", label: "Fund pages" },
  { id: "performance", label: "Portfolio performance" },
  { id: "xray", label: "X-Ray and overlap" },
  { id: "risk", label: "Risk, scenarios and rebalance" },
  { id: "journal", label: "Decision journal and scorecard" },
  { id: "quality", label: "Data-quality cockpit" },
  { id: "macro", label: "Macro and trade context" },
  { id: "estimates", label: "Analyst estimates and revisions" },
  { id: "ownership", label: "Ownership and insiders" },
  { id: "patents", label: "Patent research" },
];

export type Defect = components["schemas"]["Defect"];
export const DEFECT_STATE_LABEL: Record<Defect["state"], string> = {
  open: "Open",
  fixed: "Fixed",
  wont_fix: "Won't fix",
};

export const RESULT_LABEL: Record<AuditResult, string> = {
  match: "Matches the source",
  mismatch: "Differs from the source",
  not_checkable: "Could not be checked",
};

export function auditState(f: FeatureReadiness): { label: string; passed: boolean } {
  if (f.audit === "passed") return { label: `${f.audited_facts} fact${f.audited_facts === 1 ? "" : "s"} matched`, passed: true };
  if (f.audit === "mismatch") return { label: "A fact differs from its source", passed: false };
  if (f.audit === "thin") {
    return { label: `${f.audited_facts} matched; compare at least 3 facts on 2 subjects`, passed: false };
  }
  return { label: "Nothing compared yet", passed: false };
}

export type AuditForm = {
  subject_kind: SubjectKind;
  subject: string;
  region: string;
  feature: string;
  field: string;
  displayed: string;
  source_value: string;
  source_url: string;
  result: AuditResult;
  note: string;
};

export const EMPTY_AUDIT: AuditForm = {
  subject_kind: "security",
  subject: "",
  region: "",
  feature: "company",
  field: "",
  displayed: "",
  source_value: "",
  source_url: "",
  result: "match",
  note: "",
};

const blank = (s: string) => (s.trim() === "" ? null : s.trim());

export function buildAudit(f: AuditForm): { ok: true; body: AuditIn } | { ok: false; error: string } {
  if (f.subject.trim() === "") return { ok: false, error: "Name what you checked, for example a ticker or a fund." };
  if (f.field.trim() === "") return { ok: false, error: "Say which fact you compared, for example revenue 2025." };
  if (f.displayed.trim() === "") return { ok: false, error: "Enter what the screen showed." };
  if (f.subject_kind === "security" && f.region.trim() === "") {
    return { ok: false, error: "Enter the security's region so the regional spread can be counted." };
  }
  if (f.result !== "not_checkable") {
    if (f.source_value.trim() === "") return { ok: false, error: "Enter the value you found in the source." };
    try {
      const url = new URL(f.source_url.trim());
      if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("scheme");
    } catch {
      return { ok: false, error: "Enter the link to the source as a full web address." };
    }
  }
  return {
    ok: true,
    body: {
      subject_kind: f.subject_kind,
      subject: f.subject.trim(),
      region: blank(f.region),
      feature: f.feature.trim(),
      field: f.field.trim(),
      displayed: f.displayed.trim(),
      source_value: blank(f.source_value),
      source_url: blank(f.source_url),
      result: f.result,
      note: blank(f.note),
    },
  };
}

/** "12 of 20", with the target spelled out so a shortfall is plain. */
export function progressLabel(p: TargetProgress): string {
  return `${p.clean} of ${p.target}`;
}

export function progressShare(p: TargetProgress): number {
  return p.target === 0 ? 0 : Math.min(1, p.clean / p.target);
}

/** The pilot day as "day 12 of 30", or the closing note once it is over. */
export function pilotDayLabel(day: number, length: number, over: boolean): string {
  return over ? `Finished after ${length} days` : `Day ${day} of ${length}`;
}
