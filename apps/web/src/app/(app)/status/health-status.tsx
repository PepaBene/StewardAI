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
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>Python API</CardTitle>
        <CardDescription className="break-all font-mono">{getApiBaseUrl()}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4" aria-live="polite">
        {result.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Checking /health...
          </p>
        ) : null}

        {result.kind === "ok" ? (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-text-muted">Status</dt>
            <dd className="flex items-center gap-1.5 font-medium">
              <CircleCheckIcon aria-hidden="true" className="size-4" />
              {result.data.status}
            </dd>
            <dt className="text-text-muted">HTTP</dt>
            <dd className="num">{result.httpStatus}</dd>
            <dt className="text-text-muted">Version</dt>
            <dd className="num font-mono">{result.data.version}</dd>
            <dt className="text-text-muted">Checked at</dt>
            <dd className="num">{formatTime(result.checkedAt)}</dd>
          </dl>
        ) : null}

        {result.kind === "error" ? (
          <AlertMessage title={result.title}>
            <p>{result.detail}</p>
            <p className="num mt-1">Checked at {formatTime(result.checkedAt)}</p>
          </AlertMessage>
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
