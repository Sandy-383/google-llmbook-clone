"use client";

import type { Note, NoteKind, StudioKind } from "@/lib/api";
import { useCreateNote, useGenerateStudio, useNotes } from "@/lib/queries";

export const STUDIO_ITEMS: { kind: StudioKind; label: string; hint: string }[] = [
  { kind: "study_guide", label: "Study guide", hint: "Key concepts, glossary and a quiz" },
  { kind: "faq", label: "FAQ", hint: "Questions a newcomer would ask" },
  { kind: "briefing", label: "Briefing doc", hint: "Summary, themes, facts, open questions" },
  { kind: "timeline", label: "Timeline", hint: "Dated events and the people involved" },
];

export const NOTE_KIND_LABEL: Record<NoteKind, string> = {
  manual: "Note",
  answer: "Saved answer",
  study_guide: "Study guide",
  faq: "FAQ",
  briefing: "Briefing",
  timeline: "Timeline",
};

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function StudioPanel({
  notebookId,
  selectedSourceIds,
  onOpenNote,
}: {
  notebookId: string;
  selectedSourceIds: string[];
  onOpenNote: (noteId: string) => void;
}) {
  const notes = useNotes(notebookId);
  const generate = useGenerateStudio(notebookId);
  const create = useCreateNote(notebookId);
  const noSources = selectedSourceIds.length === 0;

  return (
    <section className="flex min-h-48 flex-col rounded-2xl border border-border bg-card lg:min-h-0">
      <h2 className="border-b border-border px-4 py-3 text-sm font-medium">Studio</h2>

      <div className="grid grid-cols-2 gap-2 border-b border-border p-3">
        {STUDIO_ITEMS.map((item) => (
          <button
            key={item.kind}
            title={noSources ? "Select a processed source first" : item.hint}
            disabled={noSources || generate.isPending}
            onClick={() =>
              generate.mutate(
                { kind: item.kind, source_ids: selectedSourceIds },
                { onSuccess: (note) => onOpenNote(note.id) },
              )
            }
            className="rounded-xl border border-border bg-background px-3 py-2.5 text-left text-xs font-medium transition hover:border-accent disabled:opacity-40"
          >
            {item.label}
          </button>
        ))}
        {generate.error && (
          <p role="alert" className="col-span-2 text-xs text-danger">
            {generate.error.message}
          </p>
        )}
      </div>

      <div className="flex items-center justify-between px-4 pb-1 pt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Notes</h3>
        <button
          onClick={() =>
            create.mutate(
              { title: "New note", content: "" },
              { onSuccess: (note) => onOpenNote(note.id) },
            )
          }
          disabled={create.isPending}
          className="rounded-md px-2 py-1 text-xs text-accent hover:bg-background"
        >
          + Add note
        </button>
      </div>

      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {notes.data?.length === 0 && (
          <li className="px-2 py-6 text-center text-xs text-muted">
            Saved answers, your own notes and studio outputs appear here.
          </li>
        )}
        {notes.data?.map((note) => (
          <li key={note.id}>
            <button
              onClick={() => onOpenNote(note.id)}
              className="w-full rounded-lg px-2 py-2 text-left hover:bg-background"
            >
              <span className="block truncate text-sm">{note.title}</span>
              <span className="block text-[11px] text-muted">
                {NOTE_KIND_LABEL[note.kind]} · <NoteStatus note={note} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function NoteStatus({ note }: { note: Note }) {
  if (note.status === "generating") return <span className="animate-pulse">Writing…</span>;
  if (note.status === "failed") return <span className="text-danger">Failed</span>;
  return <>{dateFormat.format(new Date(note.updated_at))}</>;
}
