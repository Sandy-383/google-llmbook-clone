"use client";

import { useState, type FormEvent } from "react";
import { Answer, type ShowInSource } from "@/components/answer";
import { NOTE_KIND_LABEL, STUDIO_ITEMS } from "@/components/studio-panel";
import type { Note, StudioKind } from "@/lib/api";
import { useDeleteNote, useGenerateStudio, useNotes, useUpdateNote } from "@/lib/queries";

export function NoteView({
  notebookId,
  noteId,
  onClose,
  onOpenNote,
  onShowInSource,
}: {
  notebookId: string;
  noteId: string;
  onClose: () => void;
  onOpenNote: (noteId: string) => void;
  onShowInSource: ShowInSource;
}) {
  const notes = useNotes(notebookId);
  const note = notes.data?.find((n) => n.id === noteId);
  // An empty manual note (just created) opens ready to write in; everything else for reading.
  // Derived rather than initial state: a new note may arrive a moment after this opens.
  const [editChoice, setEditing] = useState<boolean | null>(null);
  const editing = editChoice ?? (note?.kind === "manual" && !note.content);

  return (
    <section className="flex min-h-[60vh] flex-col overflow-hidden rounded-2xl border border-border bg-card lg:min-h-0">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <button
          onClick={onClose}
          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground"
        >
          ← Sources
        </button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{note?.title ?? "Note"}</span>
        {note && <NoteActions notebookId={notebookId} note={note} onClose={onClose} onEdit={() => setEditing(true)} />}
      </div>

      {!note ? (
        <p className="p-6 text-center text-sm text-muted">
          {notes.isPending ? "Loading…" : "This note no longer exists."}
        </p>
      ) : note.status === "generating" ? (
        <p className="animate-pulse p-6 text-center text-sm text-muted">
          Writing your {NOTE_KIND_LABEL[note.kind].toLowerCase()} from the selected sources. This
          usually takes a minute or two, and waits while chat answers are being written.
        </p>
      ) : note.status === "failed" ? (
        <FailedNote notebookId={notebookId} note={note} onOpenNote={onOpenNote} />
      ) : editing ? (
        <NoteEditor notebookId={notebookId} note={note} onDone={() => setEditing(false)} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <p className="mb-3 text-xs text-muted">
            {NOTE_KIND_LABEL[note.kind]}
            {note.meta?.sources?.length ? ` · from ${note.meta.sources.join(", ")}` : ""}
          </p>
          <Answer text={note.content} passages={note.passages ?? []} onShowInSource={onShowInSource} />
          {note.meta?.omitted?.length ? (
            <p className="mt-4 text-xs text-muted">
              Too much to include in full: {note.meta.omitted.join(", ")} left out.
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}

function NoteActions({
  notebookId,
  note,
  onClose,
  onEdit,
}: {
  notebookId: string;
  note: Note;
  onClose: () => void;
  onEdit: () => void;
}) {
  const remove = useDeleteNote(notebookId);
  const [copied, setCopied] = useState(false);
  const button = "rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground";

  return (
    <div className="flex shrink-0 gap-1">
      {note.status === "ready" && (
        <>
          <button
            className={button}
            onClick={async () => {
              await navigator.clipboard.writeText(note.content);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button className={button} onClick={onEdit}>
            Edit
          </button>
        </>
      )}
      <button
        className={`${button} hover:text-danger`}
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(`Delete "${note.title}"?`)) {
            remove.mutate(note.id, { onSuccess: onClose });
          }
        }}
      >
        Delete
      </button>
    </div>
  );
}

function NoteEditor({
  notebookId,
  note,
  onDone,
}: {
  notebookId: string;
  note: Note;
  onDone: () => void;
}) {
  const update = useUpdateNote(notebookId);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    update.mutate(
      { id: note.id, title: String(form.get("title")), content: String(form.get("content")) },
      { onSuccess: onDone },
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col gap-2 p-4">
      <input
        name="title"
        defaultValue={note.title}
        required
        maxLength={200}
        aria-label="Title"
        className="rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium outline-none focus:border-accent"
      />
      <textarea
        name="content"
        defaultValue={note.content}
        maxLength={50_000}
        autoFocus
        placeholder="Write in Markdown…"
        aria-label="Note"
        className="min-h-64 flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm leading-relaxed outline-none focus:border-accent"
      />
      {update.error && <p className="text-xs text-danger">{update.error.message}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onDone} className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-background">
          Cancel
        </button>
        <button
          type="submit"
          disabled={update.isPending}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-60"
        >
          Save
        </button>
      </div>
    </form>
  );
}

function FailedNote({
  notebookId,
  note,
  onOpenNote,
}: {
  notebookId: string;
  note: Note;
  onOpenNote: (noteId: string) => void;
}) {
  const generate = useGenerateStudio(notebookId);
  const remove = useDeleteNote(notebookId);
  const studio = STUDIO_ITEMS.find((item) => item.kind === note.kind);

  return (
    <div className="p-6 text-center text-sm">
      <p className="text-danger">{note.error ?? "This couldn't be written."}</p>
      {studio && (
        <button
          disabled={generate.isPending}
          onClick={() =>
            generate.mutate(
              { kind: note.kind as StudioKind, source_ids: note.meta?.source_ids ?? [] },
              {
                onSuccess: (fresh) => {
                  remove.mutate(note.id);
                  onOpenNote(fresh.id);
                },
              },
            )
          }
          className="mt-3 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground"
        >
          Try again
        </button>
      )}
      {generate.error && <p className="mt-2 text-xs text-danger">{generate.error.message}</p>}
    </div>
  );
}
