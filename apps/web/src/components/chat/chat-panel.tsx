"use client";

import { MaximizeIcon, MessageSquareIcon, PlusIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState, type MouseEvent } from "react";

import { ChatThread } from "@/components/chat/chat-thread";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ConversationOut } from "@/lib/api/client";
import { chatHref, OPEN_CHAT_EVENT } from "@/lib/chat/chat";

/** Open the slide-over chat panel from anywhere (the command palette uses this). */
export function openChatPanel() {
  window.dispatchEvent(new Event(OPEN_CHAT_EVENT));
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

  const loadConversations = useCallback(() => {
    getBrowserApiClient()
      .GET("/chat/conversations", { params: { query: { limit: 30 } } })
      .then(({ data }) => data && setConversations(data))
      .catch(() => undefined);
  }, []);

  const open = useCallback(() => {
    setOpened(true);
    if (!dialog.current?.open) dialog.current?.showModal();
    loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    window.addEventListener(OPEN_CHAT_EVENT, open);
    return () => window.removeEventListener(OPEN_CHAT_EVENT, open);
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
      <Button variant="ghost" size="sm" onClick={open} aria-haspopup="dialog">
        <MessageSquareIcon aria-hidden="true" />
        <span className="hidden sm:inline">Chat</span>
      </Button>
      <dialog
        ref={dialog}
        aria-labelledby={headingId}
        onClickCapture={onClickCapture}
        className="m-0 ml-auto h-dvh max-h-dvh w-[min(34rem,100vw)] max-w-none border-l border-border bg-surface p-0 text-text shadow-lg backdrop:bg-text/30"
      >
        <div className="flex h-full flex-col gap-3 p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 id={headingId} className="flex items-center gap-1.5 text-lg font-semibold">
              <MessageSquareIcon aria-hidden="true" className="size-4 text-text-muted" />
              Chat
            </h2>
            <div className="flex items-center gap-1">
              <Button asChild variant="ghost" size="sm">
                <Link href={chatHref(conversationId)}>
                  <MaximizeIcon aria-hidden="true" />
                  Full page
                </Link>
              </Button>
              <Button variant="ghost" size="icon" aria-label="Close the chat panel" onClick={() => dialog.current?.close()}>
                <XIcon aria-hidden="true" />
              </Button>
            </div>
          </div>
          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1 space-y-1">
              <label htmlFor={selectId} className="text-xs text-text-muted">
                Conversation
              </label>
              <select
                id={selectId}
                value={conversationId ?? ""}
                onChange={(e) => setConversationId(e.target.value ? Number(e.target.value) : null)}
                className="h-9 w-full rounded-md border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
              >
                <option value="">New conversation</option>
                {conversations.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
            </div>
            <Button variant="outline" size="sm" onClick={() => setConversationId(null)} disabled={conversationId === null}>
              <PlusIcon aria-hidden="true" />
              New
            </Button>
          </div>
          {opened ? (
            <ChatThread
              className="min-h-0 flex-1"
              conversationId={conversationId}
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
