"use client";

import { LoaderCircleIcon, Trash2Icon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ResearchBody } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { purgeSummary, shortHash, snapshotRows } from "@/lib/research/snapshot";

import { Panel } from "./report-bits";

/** What the run was started with (profile, template, prompts, config versions, digest) and the purge of its working records. */
export function RunSnapshot({
  reportId,
  body,
  onPurged,
}: {
  reportId: number;
  body: Pick<ResearchBody, "snapshot" | "template_id" | "trace_purged_at">;
  onPurged: () => void;
}) {
  const rows = body.snapshot ? snapshotRows(body.snapshot, body.template_id) : null;
  return (
    <Panel id="run-snapshot" title="Run snapshot" hint="The settings, prompts and versions this report was written with.">
      {rows ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-[13px]">
          <Row label="Profile">
            {rows.profile.name ?? rows.profile.id ?? "—"}
            {rows.profile.id && rows.profile.name ? <span className="font-mono text-xs font-normal"> ({rows.profile.id})</span> : null}
          </Row>
          <Row label="Template">{rows.template ?? "—"}</Row>
          <Row label="Taken">{formatDateTime(rows.takenAt)}</Row>
          <Row label="Plan hash">
            <code className="font-mono text-xs font-normal" title={rows.planHash}>{shortHash(rows.planHash)}</code>
          </Row>
          <div className="col-span-2 min-w-0">
            <dt className="text-xs text-text-muted">Digest</dt>
            <dd className="font-mono text-xs break-all">{rows.digest}</dd>
          </div>
          {rows.prompts.length > 0 ? (
            <div className="col-span-2 min-w-0">
              <dt className="text-xs text-text-muted">Prompts</dt>
              <dd>
                <ul className="text-xs">
                  {rows.prompts.map((p) => (
                    <li key={p.key} className="flex flex-wrap justify-between gap-2 border-t border-border-soft py-0.5">
                      <span className="font-mono">{p.key}</span>
                      <span className="font-mono text-text-muted" title={p.hash}>{shortHash(p.hash)}</span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          {rows.versions.length > 0 ? (
            <div className="col-span-2 min-w-0">
              <dt className="text-xs text-text-muted">Config versions</dt>
              <dd className="text-xs font-normal">
                {rows.versions.map((v) => `${v.domain} ${v.id === null ? "none" : `v${v.id}`}`).join(" · ")}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : (
        <p className="text-sm text-text-muted">This report was written before run snapshots were kept.</p>
      )}
      <PurgeControl reportId={reportId} purgedAt={body.trace_purged_at ?? null} onPurged={onPurged} />
    </Panel>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="font-semibold break-words">{children}</dd>
    </div>
  );
}

/** Deletes the run's working records (URL list, snippets, checkpoints, tool trace) after a confirmation step. */
function PurgeControl({ reportId, purgedAt, onPurged }: { reportId: number; purgedAt: string | null; onPurged: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function purge() {
    setPending(true);
    setMessage(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/research/{report_id}/trace/purge", {
        params: { path: { report_id: reportId } },
      });
      if (data) {
        setMessage(purgeSummary(data));
        setConfirming(false);
        onPurged();
      } else {
        setMessage(`Not deleted (HTTP ${response.status}).`);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="border-t border-border-soft pt-3 text-sm" data-print-hide>
      {purgedAt ? (
        <p className="text-text-muted">Working records were deleted {formatDateTime(purgedAt)}.</p>
      ) : confirming ? (
        <div role="alertdialog" aria-labelledby="purge-title" aria-describedby="purge-desc" className="flex flex-col gap-2">
          <p id="purge-title" className="font-medium">Delete the working records?</p>
          <p id="purge-desc" className="text-xs text-text-muted">
            This removes the URL list, web snippets, resume checkpoints and tool trace of this run. The sentences, sources and
            claim ledger stay. It cannot be undone.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void purge()} disabled={pending}>
              {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <Trash2Icon aria-hidden="true" />}
              Yes, delete
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirming(false)} disabled={pending}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
          <Trash2Icon aria-hidden="true" />
          Purge working records
        </Button>
      )}
      <p role="status" aria-live="polite" className="mt-1 text-xs">
        {message}
      </p>
    </div>
  );
}
