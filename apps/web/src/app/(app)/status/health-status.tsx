"use client";

import { CircleCheckIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBrowserApiClient, type HealthResponse } from "@/lib/api/client";
import { getApiBaseUrl } from "@/lib/env";

type HealthResult =
  | { kind: "loading" }
  | { kind: "ok"; httpStatus: number; data: HealthResponse; checkedAt: Date }
  | { kind: "error"; title: string; detail: string; checkedAt: Date };

async function checkHealth(): Promise<HealthResult> {
  const baseUrl = getApiBaseUrl();
  try {
    const { data, response } = await getBrowserApiClient().GET("/health");
    if (response.ok && data) {
      return { kind: "ok", httpStatus: response.status, data, checkedAt: new Date() };
    }
    return {
      kind: "error",
      title: `API responded with HTTP ${response.status}`,
      detail: `${baseUrl}/health did not return a healthy response.`,
      checkedAt: new Date(),
    };
  } catch (err) {
    return {
      kind: "error",
      title: "Backend unreachable",
      detail: `Could not reach ${baseUrl}/health (${
        err instanceof Error ? err.message : String(err)
      }). Check that the API is running, NEXT_PUBLIC_API_BASE_URL is correct, and the API allows this origin (CORS).`,
      checkedAt: new Date(),
    };
  }
}

function formatTime(date: Date) {
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function HealthStatus() {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<HealthResult>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    checkHealth().then((r) => {
      if (!cancelled) setResult(r);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  function retry() {
    setResult({ kind: "loading" });
    setAttempt((n) => n + 1);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>StewardAI server</CardTitle>
        <CardDescription>The service behind every page. Its address is under Details.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4" aria-live="polite">
        {result.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Checking the server…
          </p>
        ) : null}

        {result.kind === "ok" ? (
          <>
            <p className="flex items-start gap-1.5 text-sm font-medium">
              <CircleCheckIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-gain" />
              <span>
                The server is running (version <span className="num">{result.data.version}</span>). Checked at{" "}
                <span className="num">{formatTime(result.checkedAt)}</span>.
              </span>
            </p>
            <details className="text-xs text-text-muted">
              <summary className="cursor-pointer">Details</summary>
              <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
                <dt>Reported status</dt>
                <dd>{result.data.status}</dd>
                <dt>HTTP</dt>
                <dd className="num">{result.httpStatus}</dd>
                <dt>Address</dt>
                <dd className="break-all">{getApiBaseUrl()}</dd>
              </dl>
            </details>
          </>
        ) : null}

        {result.kind === "error" ? (
          <>
            <AlertMessage title="The server is not answering">
              <p>
                Pages that need data will stay empty until it is back. Start the server, or check that the address and its access settings are
                right. Checked at <span className="num">{formatTime(result.checkedAt)}</span>.
              </p>
            </AlertMessage>
            <details className="text-xs text-text-muted">
              <summary className="cursor-pointer">Details</summary>
              <p className="mt-1 break-words">
                {result.title}. {result.detail}
              </p>
            </details>
          </>
        ) : null}

        <Button
          variant="outline"
          size="sm"
          onClick={retry}
          disabled={result.kind === "loading"}
        >
          <RefreshCwIcon aria-hidden="true" />
          Check again
        </Button>
      </CardContent>
    </Card>
  );
}
