"use client";

import type { RealtimeChannel, SupabaseClient } from "@supabase/supabase-js";
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
import { getSupabaseConfig } from "@/lib/env";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import {
  describeSync,
  JOB_COLUMNS,
  STEP_COLUMNS,
  type JobRow,
  type StepRow,
  type SyncView,
} from "@/lib/sync/sync-state";

/*
 * Live portfolio-sync status for the whole app shell.
 *
 * Reads the latest `portfolio_sync` job and its current step straight from Postgres
 * (RLS: owner only) and re-reads them on every `job` / `job_step` change pushed by
 * Supabase Realtime, so a sync started anywhere (another tab, the scheduler) shows up
 * here within about a second. If Realtime is unavailable it falls back to polling.
 */

export type RealtimeState = "connecting" | "live" | "offline";

type SyncStatusValue = {
  view: SyncView;
  /** Id of the latest succeeded sync; pages refetch their data when it changes. */
  lastSuccessJobId: number | null;
  realtime: RealtimeState;
  startSync: () => Promise<void>;
  starting: boolean;
  startError: string | null;
};

const SyncStatusContext = createContext<SyncStatusValue | null>(null);

const POLL_FALLBACK_MS = 15_000;

type Snapshot = { latest: JobRow | null; lastSuccess: JobRow | null; step: StepRow | null };

async function readSyncRows(supabase: SupabaseClient): Promise<Snapshot> {
  const jobs = () =>
    supabase
      .from("job")
      .select(JOB_COLUMNS)
      .eq("job_type", "portfolio_sync")
      .order("id", { ascending: false })
      .limit(1);
  const [latestRes, successRes] = await Promise.all([
    jobs().maybeSingle<JobRow>(),
    jobs().eq("status", "succeeded").maybeSingle<JobRow>(),
  ]);
  if (latestRes.error) throw latestRes.error;
  if (successRes.error) throw successRes.error;
  const latest = latestRes.data;
  let step: StepRow | null = null;
  if (latest && latest.status !== "succeeded" && latest.status !== "failed") {
    const stepRes = await supabase
      .from("job_step")
      .select(STEP_COLUMNS)
      .eq("job_id", latest.id)
      .order("step_index", { ascending: false })
      .limit(1)
      .maybeSingle<StepRow>();
    step = stepRes.data;
  }
  return { latest, lastSuccess: successRes.data, step };
}

export function SyncStatusProvider({ children }: { children: ReactNode }) {
  const [rows, setRows] = useState<Snapshot | null>(null);
  const [realtime, setRealtime] = useState<RealtimeState>(() =>
    getSupabaseConfig() ? "connecting" : "offline",
  );
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const reloadRef = useRef<() => Promise<void>>(async () => {});

  useEffect(() => {
    if (!getSupabaseConfig()) return;
    const supabase: SupabaseClient = getSupabaseBrowserClient();
    let cancelled = false;
    let channel: RealtimeChannel | null = null;
    // Reads can overlap when events arrive in bursts: only the newest result is applied.
    let sequence = 0;

    const reload = async () => {
      const mine = ++sequence;
      try {
        const next = await readSyncRows(supabase);
        if (!cancelled && mine === sequence) setRows(next);
      } catch {
        // Keep showing the last known state; the next event or poll retries.
      }
    };
    reloadRef.current = reload;

    (async () => {
      // Join the channel with the user's token, not the anon key: RLS would hide every row.
      const { data } = await supabase.auth.getSession();
      if (cancelled) return;
      if (data.session) await supabase.realtime.setAuth(data.session.access_token);
      await reload();
      if (cancelled) return;
      channel = supabase
        .channel("portfolio-sync-status")
        .on(
          "postgres_changes",
          { event: "*", schema: "public", table: "job", filter: "job_type=eq.portfolio_sync" },
          () => void reload(),
        )
        .on("postgres_changes", { event: "*", schema: "public", table: "job_step" }, () => {
          void reload();
        })
        .subscribe((status) => {
          if (cancelled) return;
          if (status === "SUBSCRIBED") {
            setRealtime("live");
            // Catch anything that changed between the first read and the subscription.
            void reload();
          } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT" || status === "CLOSED") {
            setRealtime("offline");
          }
        });
    })();

    return () => {
      cancelled = true;
      if (channel) void supabase.removeChannel(channel);
    };
  }, []);

  // Without Realtime, poll so the indicator still moves (slower).
  useEffect(() => {
    if (realtime === "live") return;
    const timer = setInterval(() => void reloadRef.current(), POLL_FALLBACK_MS);
    return () => clearInterval(timer);
  }, [realtime]);

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
      // Show the queued job at once, even before its Realtime event arrives.
      void reloadRef.current();
    }
  }, []);

  const value = useMemo<SyncStatusValue>(
    () => ({
      view: rows ? describeSync(rows.latest, rows.lastSuccess, rows.step) : { kind: "loading" },
      lastSuccessJobId: rows?.lastSuccess?.id ?? null,
      realtime,
      startSync,
      starting,
      startError,
    }),
    [rows, realtime, startSync, starting, startError],
  );

  return <SyncStatusContext.Provider value={value}>{children}</SyncStatusContext.Provider>;
}

export function useSyncStatus(): SyncStatusValue {
  const value = useContext(SyncStatusContext);
  if (!value) throw new Error("useSyncStatus must be used inside <SyncStatusProvider>");
  return value;
}
