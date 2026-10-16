"use client";

import { MaximizeIcon, PlusIcon, SparklesIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState, type MouseEvent } from "react";

import { ChatThread } from "@/components/chat/chat-thread";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ConversationOut } from "@/lib/api/client";
import { chatHref, OPEN_CHAT_EVENT } from "@/lib/chat/chat";

/**
 * Open the slide-over chat panel from anywhere (the command palette uses this). `seed`
 * pre-fills the composer — e.g. a question about a figure the owner just selected — so
 * asking about something in place never needs the global chat box as a second step.
 */
export function openChatPanel(seed?: string) {
  window.dispatchEvent(new CustomEvent<{ seed?: string }>(OPEN_CHAT_EVENT, { detail: { seed } }));
}

/**
 * The chat panel: a header button and a slide-over dialog available on every page. A
 * native modal <dialog> keeps focus inside while open; Escape closes it. Following a link
 * to an app page (a source's evidence page, a holding, a report) closes the panel so the
 * page is in view; external sources open in a new tab.
 */
export function ChatPanel() {
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const selectId = useId();
  const [opened, setOpened] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ConversationOut[]>([]);
  const [seed, setSeed] = useState<string | null>(null);

  const loadConversations = useCallback(() => {
    getBrowserApiClient()
      .GET("/chat/conversations", { params: { query: { limit: 30 } } })
      .then(({ data }) => data && setConversations(data))
      .catch(() => undefined);
  }, []);

  const open = useCallback(
    (event?: Event) => {
      setOpened(true);
      setSeed((event as CustomEvent<{ seed?: string }> | undefined)?.detail?.seed ?? null);
      if (!dialog.current?.open) dialog.current?.showModal();
      loadConversations();
    },
    [loadConversations],
  );

  useEffect(() => {
    window.addEventListener(OPEN_CHAT_EVENT, open);
    return () => window.removeEventListener(OPEN_CHAT_EVENT, open);
  }, [open]);

  // ⌘J / Ctrl+J opens the panel from anywhere, the way ⌘K opens the command palette.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        open();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  function onClickCapture(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === dialog.current) {
      dialog.current.close();
      return;
    }
    const anchor = (event.target as HTMLElement).closest("a");
    const href = anchor?.getAttribute("href");
    if (anchor && href && href.startsWith("/") && !href.startsWith("//") && anchor.target !== "_blank") dialog.current?.close();
  }

  return (
    <>
      <Button size="sm" onClick={() => open()} aria-haspopup="dialog" aria-keyshortcuts="Control+J Meta+J" aria-label="Ask Steward">
        <SparklesIcon aria-hidden="true" />
        {/* Icon only between lg and xl, where the full nav and the actions share one row. */}
        <span aria-hidden="true" className="hidden sm:inline lg:hidden xl:inline">
          Ask Steward
        </span>
      </Button>
      <dialog
        ref={dialog}
        aria-labelledby={headingId}
        onClickCapture={onClickCapture}
        className="m-0 ml-auto h-dvh max-h-dvh w-[min(35rem,100vw)] max-w-none border-l border-border bg-surface p-0 whitespace-normal text-text shadow-2xl backdrop:bg-text/30"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center gap-2.5 border-b border-border px-5 py-4">
            <span aria-hidden="true" className="grid size-7 shrink-0 place-items-center bg-accent text-accent-foreground">
              <SparklesIcon className="size-[15px]" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id={headingId} className="text-[20px] leading-[1.1] font-semibold">
                Ask Steward
              </h2>
              <p className="text-xs text-text-muted">Answers with sources, or says it has none</p>
            </div>
            <kbd className="hidden border border-border px-1.5 font-mono text-xs text-text-muted sm:inline" title="Opens this panel from any page">
              ⌘J
            </kbd>
            <Button variant="ghost" size="icon" className="text-text-muted" aria-label="Close the chat panel" onClick={() => dialog.current?.close()}>
              <XIcon aria-hidden="true" />
            </Button>
          </div>
          <div className="flex items-center gap-2 border-b border-border px-5 py-2.5">
            <label htmlFor={selectId} className="shrink-0 text-xs text-text-muted">
              Conversation
            </label>
            <select
              id={selectId}
              value={conversationId ?? ""}
              onChange={(e) => setConversationId(e.target.value ? Number(e.target.value) : null)}
              className="h-8 min-w-0 flex-1 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
            >
              <option value="">New conversation</option>
              {conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
            <Button variant="outline" size="sm" onClick={() => setConversationId(null)} disabled={conversationId === null}>
              <PlusIcon aria-hidden="true" />
              New
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link href={chatHref(conversationId)} title="Open this conversation on the full chat page">
                <MaximizeIcon aria-hidden="true" />
                <span className="hidden sm:inline">Full page</span>
                <span className="sr-only sm:hidden">Full page</span>
              </Link>
            </Button>
          </div>
          {opened ? (
            <ChatThread
              className="min-h-0 flex-1"
              conversationId={conversationId}
              seed={seed}
              onSeedConsumed={() => setSeed(null)}
              onCreated={(id) => {
                setConversationId(id);
                loadConversations();
              }}
              onChanged={loadConversations}
            />
          ) : null}
        </div>
      </dialog>
    </>
  );
}
