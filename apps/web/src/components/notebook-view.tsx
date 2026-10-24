"use client";

import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { AppHeader, FullPageMessage } from "@/components/app-header";
import { ChatPanel, type ChatHandle } from "@/components/chat-panel";
import { NoteView } from "@/components/note-view";
import { SourceViewer, type ViewerTarget } from "@/components/source-viewer";
import { SourcesPanel } from "@/components/sources-panel";
import { StudioPanel } from "@/components/studio-panel";
import type { Passage, Source } from "@/lib/api";
import { useNotebook, useRequireUser, useSources } from "@/lib/queries";

/** What the left column shows instead of the sources list. */
type Focus = { kind: "source"; target: ViewerTarget } | { kind: "note"; noteId: string } | null;

/** Up to four suggested questions, taken in turn from each selected source's guide. */
function suggestedQuestions(sources: Source[]): string[] {
  const lists = sources.map((s) => (s.guide_status === "ready" ? (s.guide?.questions ?? []) : []));
  const picked: string[] = [];
  for (let i = 0; picked.length < 4 && lists.some((l) => l.length > i); i++) {
    for (const list of lists) if (list[i] && picked.length < 4) picked.push(list[i]);
  }
  return picked;
}

/** Notebook workspace: sources (or the open document or note), chat, studio. */
export function NotebookView() {
  const { id } = useParams<{ id: string }>();
  const me = useRequireUser();
  const notebook = useNotebook(id, me.isSuccess);
  const sources = useSources(id, me.isSuccess);
  // Track what the user switched *off*, so newly processed sources are included by default.
  const [deselected, setDeselected] = useState<ReadonlySet<string>>(new Set());
  const [focus, setFocus] = useState<Focus>(null);
  const chat = useRef<ChatHandle>(null);

  if (!me.isSuccess) {
    return <FullPageMessage>{me.isPending ? "Loading…" : "Can't reach the server."}</FullPageMessage>;
  }
  if (notebook.isError) {
    return <FullPageMessage>{notebook.error.message}</FullPageMessage>;
  }

  const ready = sources.data?.filter((s) => s.status === "ready") ?? [];
  const selected = ready.filter((s) => !deselected.has(s.id));
  const selectedIds = selected.map((s) => s.id);

  function toggle(sourceId: string) {
    setDeselected((current) => {
      const next = new Set(current);
      if (!next.delete(sourceId)) next.add(sourceId);
      return next;
    });
  }

  function show(next: Focus) {
    setFocus(next);
    // Stacked layout (phones, narrow windows): the left column is above the chat; bring it in view.
    if (next && !window.matchMedia("(min-width: 1024px)").matches) {
      requestAnimationFrame(() =>
        document.getElementById("left-panel")?.scrollIntoView({ behavior: "smooth" }),
      );
    }
  }

  const showInSource = (passage: Passage, claims: string[]) =>
    show({ kind: "source", target: { sourceId: passage.source_id, passage, claims } });
  const openNote = (noteId: string) => show({ kind: "note", noteId });

  return (
    <>
      <AppHeader user={me.data}>
        <p className="truncate font-medium">{notebook.data?.title ?? "…"}</p>
      </AppHeader>
      {/* On large screens the panels fill the viewport and scroll independently. While a
          document or note is open it takes the left column and widens. */}
      <main
        className={`grid flex-1 gap-4 p-4 lg:h-[calc(100dvh-3.5rem)] lg:overflow-hidden ${
          focus
            ? "lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_16rem]"
            : "lg:grid-cols-[20rem_minmax(0,1fr)_18rem]"
        }`}
      >
        <div id="left-panel" className="flex min-h-0 flex-col *:flex-1">
          {focus?.kind === "source" ? (
            <SourceViewer
              notebookId={id}
              source={sources.data?.find((s) => s.id === focus.target.sourceId)}
              target={focus.target}
              onClose={() => setFocus(null)}
              onAsk={(question) => chat.current?.ask(question)}
            />
          ) : focus?.kind === "note" ? (
            <NoteView
              key={focus.noteId}
              notebookId={id}
              noteId={focus.noteId}
              onClose={() => setFocus(null)}
              onOpenNote={openNote}
              onShowInSource={showInSource}
            />
          ) : (
            <SourcesPanel
              notebookId={id}
              deselected={deselected}
              onToggle={toggle}
              onSetAll={(all) => setDeselected(all ? new Set() : new Set(ready.map((s) => s.id)))}
              onOpenSource={(sourceId) => show({ kind: "source", target: { sourceId } })}
            />
          )}
        </div>
        <ChatPanel
          ref={chat}
          notebookId={id}
          selectedSourceIds={selectedIds}
          readyCount={ready.length}
          suggestions={suggestedQuestions(selected)}
          onShowInSource={showInSource}
        />
        <StudioPanel notebookId={id} selectedSourceIds={selectedIds} onOpenNote={openNote} />
      </main>
    </>
  );
}
