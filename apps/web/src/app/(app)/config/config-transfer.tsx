"use client";

import { DownloadIcon, LoaderCircleIcon, UploadIcon } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type ConfigExport, type ImportResult } from "@/lib/api/client";
import { domainFromName, domainLabel, historyHref } from "@/lib/config/domains";
import { groupByDomain, listErrors, type ListedError } from "@/lib/config/history";
import { exportFileName, parseImportFile } from "@/lib/config/transfer";
import { formatDateTime } from "@/lib/format";

const api = () => getBrowserApiClient();

type ImportState =
  | { kind: "idle" }
  | { kind: "chosen"; name: string; file: ConfigExport; domains: string[] }
  | { kind: "busy"; name: string; file: ConfigExport; domains: string[] }
  | { kind: "done"; result: ImportResult }
  | { kind: "invalid"; errors: ListedError[] }
  | { kind: "error"; message: string };

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

function DomainLink({ name, id }: { name: string; id?: number }) {
  const slug = domainFromName(name);
  const label = domainLabel(name);
  if (!slug) return <>{label}</>;
  return (
    <Link href={historyHref(slug)} className="text-accent hover:underline">
      {label}
      {id !== undefined ? <span className="num"> #{id}</span> : null}
    </Link>
  );
}

/** Export every domain's active version as one JSON file, or import such a file. */
export function ConfigTransfer() {
  const fileId = useId();
  const noteId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [exporting, setExporting] = useState<string | null>(null);
  const [busyExport, setBusyExport] = useState(false);
  const [state, setState] = useState<ImportState>({ kind: "idle" });
  const [note, setNote] = useState("");

  async function exportAll() {
    setBusyExport(true);
    setExporting(null);
    try {
      const { data, response } = await api().GET("/config/export");
      if (!data) {
        setExporting(`Export failed: the API answered HTTP ${response.status}.`);
        return;
      }
      const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportFileName();
      link.click();
      URL.revokeObjectURL(url);
      const count = Object.keys(data.domains).length;
      setExporting(`Downloaded ${count} domain${count === 1 ? "" : "s"} as ${link.download}.`);
    } catch (err) {
      setExporting(`Export failed: ${message(err)}`);
    } finally {
      setBusyExport(false);
    }
  }

  async function choose(file: File | undefined) {
    if (!file) return;
    try {
      const parsed = parseImportFile(await file.text());
      setState(parsed.ok ? { kind: "chosen", name: file.name, file: parsed.file, domains: parsed.domains } : { kind: "error", message: parsed.message });
    } catch (err) {
      setState({ kind: "error", message: `Could not read the file: ${message(err)}` });
    }
  }

  async function runImport() {
    if (state.kind !== "chosen") return;
    setState({ ...state, kind: "busy" });
    try {
      const { data, error, response } = await api().POST("/config/import", {
        body: state.file,
        params: { query: { note: note.trim() || null } },
      });
      if (data) {
        setState({ kind: "done", result: data });
        setNote("");
      } else if (response.status === 422) {
        setState({ kind: "invalid", errors: listErrors(error) });
      } else {
        setState({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      }
    } catch (err) {
      setState({ kind: "error", message: message(err) });
    } finally {
      if (input.current) input.current.value = "";
    }
  }

  const chosen = state.kind === "chosen" || state.kind === "busy" ? state : null;

  return (
    <section aria-labelledby="transfer-heading" className="space-y-4 rounded-lg border border-border bg-surface-raised p-4">
      <div>
        <h2 id="transfer-heading" className="text-[22px] leading-tight font-semibold">
          Export and import
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          One JSON file with the active version of every domain, to keep config in git or move it
          to another project. An import is checked domain by domain first; if anything is invalid,
          nothing is saved.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={() => void exportAll()} disabled={busyExport}>
          {busyExport ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <DownloadIcon aria-hidden="true" />}
          Export config
        </Button>
        {exporting ? (
          <p className="text-sm text-text-muted" role="status">
            {exporting}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor={fileId}>Import a config file</Label>
        <input
          ref={input}
          id={fileId}
          type="file"
          accept=".json,application/json"
          onChange={(e) => void choose(e.target.files?.[0])}
          className="block text-sm text-text file:mr-3 file:rounded-md file:border file:border-border file:bg-surface-raised file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:bg-surface-muted"
        />
      </div>

      {chosen ? (
        <div className="space-y-3 rounded-md border border-border p-3">
          <p className="text-sm">
            <span className="font-medium">{chosen.name}</span>
            {chosen.file.exported_at ? <span className="text-text-muted"> · exported {formatDateTime(chosen.file.exported_at)}</span> : null}
            {chosen.file.app_version ? <span className="text-text-muted"> · app {chosen.file.app_version}</span> : null}
          </p>
          <p className="text-sm text-text-muted">
            {chosen.domains.length} domain{chosen.domains.length === 1 ? "" : "s"}: {chosen.domains.map(domainLabel).join(", ")}. Each
            one that differs from its active version is saved as a new active version.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-60 flex-1 space-y-1">
              <Label htmlFor={noteId}>Note (optional)</Label>
              <Input id={noteId} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="Imported from file" />
            </div>
            <Button onClick={() => void runImport()} disabled={state.kind === "busy"}>
              {state.kind === "busy" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <UploadIcon aria-hidden="true" />}
              Import
            </Button>
            <Button
              variant="ghost"
              disabled={state.kind === "busy"}
              onClick={() => {
                setState({ kind: "idle" });
                if (input.current) input.current.value = "";
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {state.kind === "done" ? (
        <AlertMessage tone="info" title="Imported">
          {Object.keys(state.result.saved).filter((d) => !state.result.unchanged.includes(d)).length > 0 ? (
            <p>
              Saved as new active versions:{" "}
              {Object.entries(state.result.saved)
                .filter(([d]) => !state.result.unchanged.includes(d))
                .map(([d, id], i) => (
                  <span key={d}>
                    {i > 0 ? ", " : null}
                    <DomainLink name={d} id={id} />
                  </span>
                ))}
              .
            </p>
          ) : null}
          {state.result.unchanged.length > 0 ? (
            <p>
              Unchanged (already active):{" "}
              {state.result.unchanged.map((d, i) => (
                <span key={d}>
                  {i > 0 ? ", " : null}
                  <DomainLink name={d} />
                </span>
              ))}
              .
            </p>
          ) : null}
        </AlertMessage>
      ) : null}

      {state.kind === "invalid" ? (
        <AlertMessage title={`The file was rejected: ${state.errors.length} problem${state.errors.length === 1 ? "" : "s"}. Nothing was saved.`}>
          {groupByDomain(state.errors).map((group) => (
            <div key={group.domain ?? "file"} className="mt-1">
              <p className="font-medium text-text">{group.domain ?? "File"}</p>
              <ul className="list-disc space-y-0.5 pl-4">
                {group.errors.map((e, i) => (
                  <li key={`${i}:${e.path}`}>
                    {e.path ? <span className="font-mono text-xs">{e.path}</span> : null} {e.message}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </AlertMessage>
      ) : null}

      {state.kind === "error" ? <AlertMessage title="Import failed">{state.message}</AlertMessage> : null}
    </section>
  );
}
