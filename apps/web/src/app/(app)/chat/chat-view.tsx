"use client";

import { LoaderCircleIcon, MessageSquareIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ChatThread } from "@/components/chat/chat-thread";
import { AlertMessage } from "@/components/alert-message";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ConversationOut } from "@/lib/api/client";
import { chatHref } from "@/lib/chat/chat";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: ConversationOut[] };

/** The chat with the copilot on a full page: the conversation list and the open conversation (`?c=`). */
export function ChatView() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = Number(params.get("c"));
  const conversationId = Number.isSafeInteger(raw) && raw > 0 ? raw : null;
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  const refresh = useCallback(() => {
    getBrowserApiClient()
      .GET("/chat/conversations", { params: { query: { limit: 100 } } })
      .then(({ data, response }) => {
        if (data) setLoad({ kind: "ok", items: data });
        else setLoad((prev) => (prev.kind === "ok" ? prev : { kind: "error", message: `The API answered HTTP ${response.status}.` }));
      })
      .catch((err: unknown) =>
        setLoad((prev) => (prev.kind === "ok" ? prev : { kind: "error", message: err instanceof Error ? err.message : String(err) })),
      );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="space-y-6">
      <PageHeader
        kicker="Ask Steward"
        title="Chat"
        description={
          <p className="max-w-prose">
            Ask about the portfolio, a holding, the news or any report. Answers read the app&rsquo;s own records (and
            tier-1/2 outlets when needed); every sentence links to the same evidence and holding pages as the rest of the
            app. The chat is also a panel on every page (Ask Steward in the header, ⌘J, or &ldquo;Open chat&rdquo; in the
            command palette).
          </p>
        }
      />
      <div className="grid items-start gap-5 lg:grid-cols-[19rem_minmax(0,1fr)]">
        <Blueprint as="div" className="space-y-3 p-4">
          <nav aria-label="Conversations" className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[20px] leading-tight font-semibold">Conversations</h2>
            <span className="flex gap-1">
              <Button variant="ghost" size="icon" aria-label="Reload the conversations" onClick={refresh}>
                <RefreshCwIcon aria-hidden="true" />
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href={chatHref()}>
                  <PlusIcon aria-hidden="true" />
                  New
                </Link>
              </Button>
            </span>
          </div>
          {load.kind === "loading" ? (
            <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
              <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
              Loading…
            </p>
          ) : null}
          {load.kind === "error" ? <AlertMessage title="Could not load the conversations">{load.message}</AlertMessage> : null}
          {load.kind === "ok" && load.items.length === 0 ? <p className="text-sm text-text-muted">No conversations yet.</p> : null}
          {load.kind === "ok" && load.items.length > 0 ? (
            <ul className="space-y-1">
              {load.items.map((c) => (
                <li key={c.id}>
                  <Link
                    href={chatHref(c.id)}
                    aria-current={c.id === conversationId ? "page" : undefined}
                    className={cn(
                      "block space-y-0.5 border-l-2 px-3 py-2 text-sm",
                      c.id === conversationId ? "border-accent bg-accent-soft" : "border-transparent hover:bg-surface-muted",
                    )}
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      <MessageSquareIcon aria-hidden="true" className="size-3.5 shrink-0 text-text-muted" />
                      <span className="truncate">{c.title}</span>
                    </span>
                    {c.last_message ? <span className="line-clamp-2 block text-xs text-text-muted">{c.last_message}</span> : null}
                    <time dateTime={c.updated_at} className="num block text-xs text-text-muted">
                      {formatDateTime(c.updated_at)}
                    </time>
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
          </nav>
        </Blueprint>
        <Blueprint aria-label="Conversation" className="flex h-[72vh] min-h-96 flex-col">
          <ChatThread
            key={conversationId ?? "new"}
            className="min-h-0 flex-1"
            conversationId={conversationId}
            onCreated={(id) => {
              router.replace(chatHref(id), { scroll: false });
              refresh();
            }}
            onChanged={refresh}
          />
        </Blueprint>
      </div>
    </div>
  );
}
