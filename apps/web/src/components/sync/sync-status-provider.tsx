"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { describeSync, type JobRow, type StepRow, type SyncView } from "@/lib/sync/sync-state";

/*
 * Live portfolio-sync status for the whole app shell.
 *
 * Reads the latest `portfolio_sync` job and its current step from the backend API and polls:
 * every few seconds while a sync is queued or running, slowly otherwise, and at once when the
 * tab becomes visible again. A sync started anywhere (another tab, the scheduler) shows up
 * within one poll.
 */

type SyncStatusValue = {
  view: SyncView;
  /** Id of the latest succeeded sync; pages refetch their data when it changes. */
  lastSuccessJobId: number | null;
  startSync: () => Promise<void>;
  starting: boolean;
  startError: string | null;
};

const SyncStatusContext = createContext<SyncStatusValue | null>(null);

const POLL_ACTIVE_MS = 3_000;
const POLL_IDLE_MS = 15_000;

type Snapshot = { latest: JobRow | null; lastSuccess: JobRow | null; step: StepRow | null };

async function readSyncRows(): Promise<Snapshot> {
  const api = getBrowserApiClient();
  const latestJobs = (status?: "succeeded") =>
    api.GET("/jobs", {
      params: {
        query: { job_type: ["portfolio_sync"], limit: 1, ...(status ? { status: [status] } : {}) },
      },
    });
  const [latestRes, successRes] = await Promise.all([latestJobs(), latestJobs("succeeded")]);
  if (!latestRes.response.ok || !latestRes.data) throw new Error(`HTTP ${latestRes.response.status}`);
  if (!successRes.response.ok || !successRes.data) {
    throw new Error(`HTTP ${successRes.response.status}`);
  }
  const latest = latestRes.data.jobs[0] ?? null;
  let step: StepRow | null = null;
  if (latest && latest.status !== "succeeded" && latest.status !== "failed") {
    const detail = await api.GET("/jobs/{job_id}", { params: { path: { job_id: latest.id } } });
    const last = detail.data?.steps.at(-1);
    if (last) {
      step = { job_id: latest.id, step_index: last.step_index, name: last.name, status: last.status };
    }
  }
  return { latest, lastSuccess: successRes.data.jobs[0] ?? null, step };
}

export function SyncStatusProvider({ children }: { children: ReactNode }) {
  const [rows, setRows] = useState<Snapshot | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const reloadRef = useRef<() => Promise<void>>(async () => {});
  const active = rows?.latest
    ? rows.latest.status !== "succeeded" && rows.latest.status !== "failed"
    : false;

  useEffect(() => {
    let cancelled = false;
    // Reads can overlap (a poll and a manual refresh): only the newest result is applied.
    let sequence = 0;
    const reload = async () => {
      const mine = ++sequence;
      try {
        const next = await readSyncRows();
        if (!cancelled && mine === sequence) {
          setRows(next);
          setReadFailed(false);
        }
      } catch {
        // Keep the last known state if there is one; the next poll retries.
        if (!cancelled && mine === sequence) setReadFailed(true);
      }
    };
    reloadRef.current = reload;
    void reload();
    const onVisible = () => {
      if (document.visibilityState === "visible") void reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(
      () => {
        if (document.visibilityState === "visible") void reloadRef.current();
      },
      active ? POLL_ACTIVE_MS : POLL_IDLE_MS,
    );
    return () => clearInterval(timer);
  }, [active]);

  const startSync = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    try {
      const { response, error } = await getBrowserApiClient().POST("/jobs/portfolio-sync");
      if (!response.ok) {
        // The spec declares no error body for this route; FastAPI still sends {"detail": ...}.
        const body: unknown = error;
        const detail =
          body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
        setStartError(`Could not start the sync (HTTP ${response.status}${detail ? `: ${detail}` : ""}).`);
      }
    } catch (err) {
      setStartError(
        `Could not reach the API to start the sync (${err instanceof Error ? err.message : String(err)}).`,
      );
    } finally {
      setStarting(false);
      // Show the queued job at once instead of waiting for the next poll.
      void reloadRef.current();
    }
  }, []);

  const value = useMemo<SyncStatusValue>(
    () => ({
      view: rows
        ? describeSync(rows.latest, rows.lastSuccess, rows.step)
        : readFailed
          ? { kind: "unavailable" }
          : { kind: "loading" },
      lastSuccessJobId: rows?.lastSuccess?.id ?? null,
      startSync,
      starting,
      startError,
    }),
    [rows, readFailed, startSync, starting, startError],
  );

  return <SyncStatusContext.Provider value={value}>{children}</SyncStatusContext.Provider>;
}

export function useSyncStatus(): SyncStatusValue {
  const value = useContext(SyncStatusContext);
  if (!value) throw new Error("useSyncStatus must be used inside <SyncStatusProvider>");
  return value;
}
