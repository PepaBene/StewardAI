"use client";

import { useState } from "react";

import { ChatThread } from "@/components/chat/chat-thread";
import { NewsTabs } from "@/components/news/news-tabs";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";

const EXAMPLES = [
  "What is the Japanese press saying about yen intervention this week?",
  "Who reported the guidance cut first, and was it confirmed by a primary source?",
  "What changed in the latest developments on my largest holding?",
  "Which of my holdings are reached by news from Taiwan?",
];

/** Ask: the portfolio chat, opened on the news. It reads the wire, stories and threads, and cites each one. */
export function AskView() {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [seed, setSeed] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Ask"
        description={
          <span className="block max-w-prose">
            Ask about coverage: who said what, who reported first, what a country&rsquo;s press is saying, what changed in a
            situation. The answer searches the stored wire and stories, shows each outlet&rsquo;s tier, and links its
            sources. It is the same chat as the panel on every page, so the conversation is kept there too.
          </span>
        }
      />
      <NewsTabs active="ask" />
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label="Example questions">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button
              type="button"
              onClick={() => setSeed(q)}
              className="rounded-md border border-border px-3 py-1.5 text-left text-sm text-text-muted hover:bg-border-soft hover:text-text"
            >
              {q}
            </button>
          </li>
        ))}
      </ul>
      <Blueprint aria-label="Conversation" className="flex h-[68vh] min-h-96 flex-col">
        <ChatThread
          key={conversationId ?? "new"}
          className="min-h-0 flex-1"
          conversationId={conversationId}
          seed={seed}
          onSeedConsumed={() => setSeed(null)}
          onCreated={(id) => setConversationId(id)}
        />
      </Blueprint>
    </div>
  );
}
