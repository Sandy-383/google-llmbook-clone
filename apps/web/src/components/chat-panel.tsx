"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from "react";
import { Answer, type ShowInSource } from "@/components/answer";
import type { ChatMessage, Passage } from "@/lib/api";
import { streamChat } from "@/lib/chat-stream";
import { queryKeys, useClearMessages, useMessages, useSaveAnswer } from "@/lib/queries";

/** The answer being written right now; replaced by the saved message when it finishes. */
type Live = {
  question: string;
  query: string | null;
  passages: Passage[] | null;
  text: string;
  error: string | null;
};

/** Lets other panels ask a question here, e.g. a suggested question from a source guide. */
export type ChatHandle = { ask: (question: string) => void };

export function ChatPanel({
  ref,
  notebookId,
  selectedSourceIds,
  readyCount,
  suggestions,
  onShowInSource,
}: {
  ref?: Ref<ChatHandle>;
  notebookId: string;
  selectedSourceIds: string[];
  readyCount: number;
  /** Suggested questions from the selected sources' guides, shown while the chat is empty. */
  suggestions: string[];
  onShowInSource: ShowInSource;
}) {
  const client = useQueryClient();
  const messages = useMessages(notebookId);
  const clear = useClearMessages(notebookId);
  const [live, setLive] = useState<Live | null>(null);
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [messages.data, live?.text, live?.passages]);

  // Leaving the page mid-answer stops generation (the partial answer is kept).
  useEffect(() => () => abort.current?.abort(), []);

  const canAsk = selectedSourceIds.length > 0 && !live;

  useImperativeHandle(ref, () => ({
    ask: (question: string) => {
      if (canAsk) void ask(question);
      else setDraft(question); // busy: leave it in the box to send next
    },
  }));

  async function refreshMessages() {
    await client.invalidateQueries({ queryKey: queryKeys.messages(notebookId) });
  }

  async function ask(question: string) {
    const controller = new AbortController();
    abort.current = controller;
    setSendError(null);
    setDraft("");
    setLive({ question, query: null, passages: null, text: "", error: null });
    try {
      await streamChat(
        notebookId,
        { message: question, source_ids: selectedSourceIds },
        {
          signal: controller.signal,
          onEvent: (e) =>
            setLive((current) => {
              if (!current) return current;
              switch (e.event) {
                case "searching":
                  return { ...current, query: e.data.query };
                case "sources":
                  return { ...current, passages: e.data.passages };
                case "token":
                  return { ...current, text: current.text + e.data.text };
                case "error":
                  return { ...current, error: e.data.detail };
                default:
                  return current;
              }
            }),
        },
      );
      await refreshMessages();
      setLive(null);
    } catch (err) {
      if (controller.signal.aborted) {
        // The server saves what was written so far; give it a moment, then show that.
        await new Promise((r) => setTimeout(r, 400));
        await refreshMessages();
        setLive(null);
      } else {
        // Rejected before anything was saved (busy, rate limit, offline): let them retry.
        setLive(null);
        setDraft(question);
        setSendError(err instanceof Error ? err.message : "Couldn't send the question.");
        await refreshMessages();
      }
    } finally {
      abort.current = null;
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const question = draft.trim();
    if (question && canAsk) void ask(question);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) onSubmit(e);
  }

  const history = messages.data ?? [];

  return (
    <section className="flex min-h-96 flex-col rounded-2xl border border-border bg-card lg:min-h-0">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium">Chat</h2>
        {history.length > 0 && (
          <button
            onClick={() => window.confirm("Clear this notebook's chat?") && clear.mutate()}
            disabled={!!live || clear.isPending}
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground disabled:opacity-50"
          >
            Clear chat
          </button>
        )}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-4 py-5 sm:px-6">
        {history.length === 0 && !live && (
          <div className="mx-auto max-w-md py-10 text-center text-sm text-muted">
            {readyCount === 0 ? (
              <p>Add a source to start asking questions.</p>
            ) : (
              <>
                <p className="font-medium text-foreground">Ask anything about your sources</p>
                <p className="mt-1">
                  Answers come only from the {selectedSourceIds.length} selected source
                  {selectedSourceIds.length === 1 ? "" : "s"}, with numbered citations you can
                  click to see the exact passage.
                </p>
                {suggestions.length > 0 && (
                  <div className="mt-6 space-y-2 text-left">
                    <p className="text-xs font-medium uppercase tracking-wide">Try asking</p>
                    {suggestions.map((question) => (
                      <button
                        key={question}
                        onClick={() => canAsk && void ask(question)}
                        disabled={!canAsk}
                        className="block w-full rounded-xl border border-border bg-background px-3 py-2 text-left text-sm text-foreground hover:border-accent disabled:opacity-50"
                      >
                        {question}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {history.map((m) => (
          <MessageView
            key={m.id}
            notebookId={notebookId}
            message={m}
            onShowInSource={onShowInSource}
          />
        ))}

        {live && (
          <>
            <UserBubble text={live.question} />
            <div>
              {live.text ? (
                <Answer text={live.text} passages={live.passages ?? []} onShowInSource={onShowInSource} />
              ) : (
                !live.error && (
                  <p className="animate-pulse text-sm text-muted">
                    {live.passages
                      ? `Reading ${live.passages.length} passage${live.passages.length === 1 ? "" : "s"}…`
                      : live.query
                        ? `Searching your sources for “${live.query}”…`
                        : "Searching your sources…"}
                  </p>
                )
              )}
              {live.error && <p className="mt-2 text-sm text-danger">{live.error}</p>}
            </div>
          </>
        )}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="border-t border-border p-3">
        {sendError && <p className="mb-2 px-1 text-xs text-danger">{sendError}</p>}
        <div className="flex items-end gap-2 rounded-xl border border-border bg-background p-2 focus-within:border-accent">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={Math.min(6, Math.max(1, draft.split("\n").length))}
            maxLength={4000}
            disabled={selectedSourceIds.length === 0}
            placeholder={
              selectedSourceIds.length === 0
                ? "Add a source first"
                : "Ask about your sources…"
            }
            aria-label="Your question"
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none disabled:cursor-not-allowed"
          />
          {live ? (
            <button
              type="button"
              onClick={() => abort.current?.abort()}
              className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-card"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!canAsk || !draft.trim()}
              className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-40"
            >
              Ask
            </button>
          )}
        </div>
        <p className="mt-1.5 px-1 text-[11px] text-muted">
          Answers are generated on this computer from your sources and can still be wrong. Check
          the cited passages.
        </p>
      </form>
    </section>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent/15 px-4 py-2 text-sm">
        {text}
      </p>
    </div>
  );
}

function MessageView({
  notebookId,
  message,
  onShowInSource,
}: {
  notebookId: string;
  message: ChatMessage;
  onShowInSource: ShowInSource;
}) {
  if (message.role === "user") return <UserBubble text={message.content} />;
  return (
    <div>
      {message.content && (
        <Answer text={message.content} passages={message.passages ?? []} onShowInSource={onShowInSource} />
      )}
      {message.status === "stopped" && <p className="mt-1 text-xs text-muted">Stopped.</p>}
      {message.status === "error" && (
        <p className="mt-1 text-sm text-danger">{message.meta?.error ?? "This answer failed."}</p>
      )}
      {message.status === "complete" && !message.meta?.not_found && (
        <SaveToNote notebookId={notebookId} messageId={message.id} />
      )}
    </div>
  );
}

function SaveToNote({ notebookId, messageId }: { notebookId: string; messageId: string }) {
  const save = useSaveAnswer(notebookId);
  return (
    <button
      onClick={() => save.mutate(messageId)}
      disabled={save.isPending || save.isSuccess}
      className="mt-2 rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground disabled:hover:bg-transparent"
    >
      {save.isSuccess ? "Saved to notes ✓" : save.error ? save.error.message : "Save to note"}
    </button>
  );
}
