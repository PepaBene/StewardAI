import { describe, expect, it } from "vitest";

import { describeSync, type JobRow, type StepRow } from "./sync-state";

function job(id: number, status: JobRow["status"], extra: Partial<JobRow> = {}): JobRow {
  return {
    id,
    status,
    trigger: "manual",
    created_at: "2026-09-25T20:30:00Z",
    started_at: null,
    finished_at: null,
    error: null,
    ...extra,
  };
}

const step = (job_id: number, name: string): StepRow => ({
  job_id,
  step_index: 3,
  name,
  status: "running",
});

describe("describeSync", () => {
  it("reports that nothing has synced yet", () => {
    expect(describeSync(null, null, null)).toEqual({ kind: "never" });
  });

  it("shows the running step of the latest job", () => {
    expect(describeSync(job(7, "running"), null, step(7, "fx_rates"))).toEqual({
      kind: "active",
      phase: "running",
      stepLabel: "Fetching FX rates",
      jobId: 7,
    });
  });

  it("ignores a step that belongs to an older job", () => {
    const view = describeSync(job(8, "waiting"), null, step(7, "snapshot"));
    expect(view).toMatchObject({ kind: "active", phase: "waiting", stepLabel: null });
  });

  it("keeps the last success visible after a failure", () => {
    const success = job(5, "succeeded", { finished_at: "2026-09-24T20:31:00Z" });
    const failed = job(6, "failed", { finished_at: "2026-09-25T20:31:00Z", error: "429" });
    expect(describeSync(failed, success, null)).toEqual({
      kind: "idle",
      lastSuccessAt: "2026-09-24T20:31:00Z",
      failure: { at: "2026-09-25T20:31:00Z", error: "429" },
    });
  });

  it("is idle with no failure after a success", () => {
    const success = job(5, "succeeded", { finished_at: "2026-09-25T20:31:00Z" });
    expect(describeSync(success, success, null)).toEqual({
      kind: "idle",
      lastSuccessAt: "2026-09-25T20:31:00Z",
      failure: null,
    });
  });
});
