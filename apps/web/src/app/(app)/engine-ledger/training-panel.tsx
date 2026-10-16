"use client";

import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { label } from "@/lib/labels";
import { kindName } from "@/lib/oversight/gates";

type Summary = components["schemas"]["TrainingSummary"];

/** What is being kept so models can be fine-tuned later: recorded AI calls and your judgements. */
export function TrainingPanel() {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getBrowserApiClient()
      .GET("/engine/training")
      .then((r) => {
        if (r.data) setS(r.data);
        else setError(`Could not load what is collected (HTTP ${r.response.status}).`);
      });
  }, []);

  return (
    <Blueprint>
      <div className="space-y-3 p-5">
        <h2 className="panel-title">What is kept for fine-tuning</h2>
        <p className="max-w-prose text-sm text-text-muted">
          Every AI call (chat included) is stored with secrets and account numbers removed, and your
          decisions on the engine&apos;s work are kept as labels. Nothing is trained or sent anywhere.
          Recording stops by itself at the storage limit; the limit and the per-job switches are in
          the jobs config under training capture.
        </p>
        {error ? <AlertMessage title="Not loaded">{error}</AlertMessage> : null}
        {s ? (
          <div className="grid max-w-3xl gap-6 sm:grid-cols-2">
            <div className="space-y-2">
              <p className="text-sm">
                <span className="font-medium">{s.calls.toLocaleString()}</span> calls recorded (
                {s.last_30_days.toLocaleString()} in the last 30 days), {s.size_mb} MB
              </p>
              <ul className="space-y-1 text-sm text-text-muted">
                {s.by_job_type.map((c) => (
                  <li key={c.job_type} className="flex justify-between border-b border-border-soft py-1">
                    <span>{label("job", c.job_type)}</span>
                    <span>
                      {c.calls.toLocaleString()} ({c.last_30_days.toLocaleString()} recent)
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="space-y-2">
              <p className="text-sm">
                <span className="font-medium">{s.labels.toLocaleString()}</span> labels (
                {s.owner_labels.toLocaleString()} from you)
              </p>
              <ul className="space-y-1 text-sm text-text-muted">
                {s.by_subject.map((l) => (
                  <li key={l.subject_kind} className="flex justify-between border-b border-border-soft py-1">
                    <span>{kindName(l.subject_kind)}</span>
                    <span>
                      {l.right} right, {l.wrong} wrong, {l.corrected} corrected
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
      </div>
    </Blueprint>
  );
}
