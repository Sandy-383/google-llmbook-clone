"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AppHeader, FullPageMessage } from "@/components/app-header";
import type { Notebook } from "@/lib/api";
import {
  useCreateNotebook,
  useDeleteNotebook,
  useNotebooks,
  useRenameNotebook,
  useRequireUser,
} from "@/lib/queries";

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

export function NotebookDashboard() {
  const router = useRouter();
  const me = useRequireUser();
  const notebooks = useNotebooks(me.isSuccess);
  const create = useCreateNotebook();

  if (!me.isSuccess) {
    return <FullPageMessage>{me.isPending ? "Loading…" : "Can't reach the server."}</FullPageMessage>;
  }

  const createNotebook = () =>
    create.mutate("Untitled notebook", {
      onSuccess: (notebook) => router.push(`/notebooks/${notebook.id}`),
    });

  return (
    <>
      <AppHeader user={me.data} />
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-10 sm:px-6">
        <div className="mb-8 flex items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold tracking-tight">Your notebooks</h1>
          <button
            onClick={createNotebook}
            disabled={create.isPending}
            className="rounded-full bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition hover:opacity-90 disabled:opacity-60"
          >
            + New notebook
          </button>
        </div>

        {create.error && (
          <p role="alert" className="mb-4 text-sm text-danger">
            {create.error.message}
          </p>
        )}

        {notebooks.isPending ? (
          <p className="text-sm text-muted">Loading notebooks…</p>
        ) : notebooks.isError ? (
          <p role="alert" className="text-sm text-danger">
            {notebooks.error.message}
          </p>
        ) : notebooks.data.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border p-12 text-center">
            <p className="font-medium">No notebooks yet</p>
            <p className="mt-1 text-sm text-muted">
              Create one, add your PDFs and documents, then ask questions about them.
            </p>
          </div>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {notebooks.data.map((notebook) => (
              <NotebookCard key={notebook.id} notebook={notebook} />
            ))}
          </ul>
        )}
      </main>
    </>
  );
}

function NotebookCard({ notebook }: { notebook: Notebook }) {
  const [editing, setEditing] = useState(false);
  const rename = useRenameNotebook();
  const remove = useDeleteNotebook();
  const error = rename.error ?? remove.error;

  function onRename(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const title = String(new FormData(e.currentTarget).get("title")).trim();
    if (!title || title === notebook.title) {
      setEditing(false);
      return;
    }
    rename.mutate({ id: notebook.id, title }, { onSuccess: () => setEditing(false) });
  }

  function onDelete() {
    if (window.confirm(`Delete "${notebook.title}"? This can't be undone.`)) {
      remove.mutate(notebook.id);
    }
  }

  return (
    <li className="flex min-h-36 flex-col rounded-2xl border border-border bg-card p-5 shadow-sm transition hover:shadow-md">
      {editing ? (
        <form onSubmit={onRename} className="flex-1">
          <input
            name="title"
            defaultValue={notebook.title}
            maxLength={200}
            autoFocus
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            className="w-full rounded-lg border border-border bg-background px-2 py-1 font-medium outline-none focus:border-accent"
          />
          <p className="mt-2 text-xs text-muted">Enter to save · Esc to cancel</p>
        </form>
      ) : (
        <Link href={`/notebooks/${notebook.id}`} className="flex-1">
          <h2 className="line-clamp-2 font-medium">{notebook.title}</h2>
          <p className="mt-1 text-xs text-muted">
            Edited {dateFormat.format(new Date(notebook.updated_at))}
          </p>
        </Link>
      )}

      {error && <p className="mt-2 text-xs text-danger">{error.message}</p>}

      <div className="mt-4 flex gap-1 text-xs">
        <button
          onClick={() => setEditing(true)}
          className="rounded-md px-2 py-1 text-muted hover:bg-background hover:text-foreground"
        >
          Rename
        </button>
        <button
          onClick={onDelete}
          disabled={remove.isPending}
          className="rounded-md px-2 py-1 text-muted hover:bg-background hover:text-danger"
        >
          Delete
        </button>
      </div>
    </li>
  );
}
