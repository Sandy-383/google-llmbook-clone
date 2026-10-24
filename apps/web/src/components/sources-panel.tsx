"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { ACCEPTED_EXTENSIONS, ApiError, UPLOAD_LIMIT_MB, type Source } from "@/lib/api";
import {
  useAddTextSource,
  useDeleteSource,
  useRetrySource,
  useSources,
  useUploadSource,
} from "@/lib/queries";

/** A file being uploaded, or one that failed: kept (with the file) so it can be retried. */
type LocalUpload = { key: string; file: File; error?: string; retryable?: boolean };

// fetch() rejects with a bare "Failed to fetch" when the request never got an answer.
const NETWORK_ERROR =
  "The upload didn't reach the server. Check your connection and retry. On a phone, if the " +
  "file is in Google Drive or another cloud app, download it to the phone first.";

const KIND_LABEL: Record<Source["kind"], string> = {
  pdf: "PDF",
  docx: "DOC",
  text: "TXT",
  markdown: "MD",
};

function clientSideProblem(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return "Unsupported file type. Use PDF, Word (.docx), .txt or .md.";
  }
  if (file.size > UPLOAD_LIMIT_MB * 1024 * 1024) {
    return `Too large. The limit is ${UPLOAD_LIMIT_MB} MB.`;
  }
  if (file.size === 0) return "The file is empty.";
  return null;
}

export function SourcesPanel({
  notebookId,
  deselected,
  onToggle,
  onSetAll,
  onOpenSource,
}: {
  notebookId: string;
  deselected: ReadonlySet<string>;
  onToggle: (sourceId: string) => void;
  onSetAll: (selected: boolean) => void;
  onOpenSource: (sourceId: string) => void;
}) {
  const sources = useSources(notebookId, true);
  const uploadSource = useUploadSource(notebookId);
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const pasteDialog = useRef<HTMLDialogElement>(null);

  async function upload(key: string, file: File) {
    setUploads((u) => u.map((x) => (x.key === key ? { ...x, error: undefined } : x)));
    try {
      await uploadSource.mutateAsync(file);
      setUploads((u) => u.filter((x) => x.key !== key));
    } catch (err) {
      // Server answers (too large, unsupported, duplicate) are final; a lost connection isn't.
      const network = !(err instanceof ApiError);
      const error = network ? NETWORK_ERROR : err.message;
      setUploads((u) =>
        u.map((x) => (x.key === key ? { ...x, error, retryable: network } : x)),
      );
    }
  }

  async function addFiles(files: FileList | File[]) {
    // Sequential: keeps memory and server load flat when someone drops a whole folder.
    for (const file of Array.from(files)) {
      const key = `${file.name}-${crypto.randomUUID()}`;
      const problem = clientSideProblem(file);
      setUploads((u) => [...u, { key, file, error: problem ?? undefined }]);
      if (!problem) await upload(key, file);
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files);
  }

  const ready = sources.data?.filter((s) => s.status === "ready") ?? [];
  const selectedCount = ready.filter((s) => !deselected.has(s.id)).length;

  return (
    <section
      className="relative flex min-h-48 flex-col rounded-2xl border border-border bg-card lg:min-h-0"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium">Sources</h2>
        <div className="flex gap-1">
          <button
            onClick={() => pasteDialog.current?.showModal()}
            className="rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground"
          >
            Paste text
          </button>
          <button
            onClick={() => fileInput.current?.click()}
            className="rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-accent-foreground hover:opacity-90"
          >
            + Add
          </button>
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={(e) => {
            if (e.target.files?.length) void addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {ready.length > 1 && (
        <label className="flex items-center gap-2 border-b border-border px-4 py-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={selectedCount === ready.length}
            onChange={(e) => onSetAll(e.target.checked)}
            className="accent-accent"
          />
          Use all sources ({selectedCount} of {ready.length} selected)
        </label>
      )}

      <ul className="flex-1 space-y-1 overflow-y-auto p-2">
        {sources.data?.map((source) => (
          <SourceRow
            key={source.id}
            notebookId={notebookId}
            source={source}
            selected={!deselected.has(source.id)}
            onToggle={() => onToggle(source.id)}
            onOpen={() => onOpenSource(source.id)}
          />
        ))}
        {uploads.map((u) => (
          <li key={u.key} className="rounded-lg px-2 py-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate">{u.file.name}</span>
              {u.error && (
                <button
                  onClick={() => setUploads((all) => all.filter((x) => x.key !== u.key))}
                  className="text-xs text-muted hover:text-foreground"
                  aria-label={`Dismiss ${u.file.name}`}
                >
                  ✕
                </button>
              )}
            </div>
            <p className={`text-xs ${u.error ? "text-danger" : "text-muted"}`}>
              {u.error ?? `Uploading ${(u.file.size / 1024 / 1024).toFixed(1)} MB…`}
              {u.error && u.retryable && (
                <>
                  {" "}
                  <button
                    onClick={() => void upload(u.key, u.file)}
                    className="font-medium text-accent hover:underline"
                  >
                    Retry
                  </button>
                </>
              )}
            </p>
          </li>
        ))}
      </ul>

      {sources.isSuccess && sources.data.length === 0 && uploads.length === 0 && (
        <p className="absolute inset-x-6 top-1/2 -translate-y-1/3 text-center text-sm text-muted">
          Drop PDFs, Word documents or text files here, or use <b>+ Add</b>.
          <br />
          Everything stays on this computer.
        </p>
      )}
      {sources.isError && (
        <p role="alert" className="p-4 text-sm text-danger">
          {sources.error.message}
        </p>
      )}

      {dragging && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-2xl border-2 border-dashed border-accent bg-card/90 text-sm font-medium">
          Drop files to add them
        </div>
      )}

      <PasteTextDialog ref={pasteDialog} notebookId={notebookId} />
    </section>
  );
}

function SourceRow({
  notebookId,
  source,
  selected,
  onToggle,
  onOpen,
}: {
  notebookId: string;
  source: Source;
  selected: boolean;
  onToggle: () => void;
  onOpen: () => void;
}) {
  const remove = useDeleteSource(notebookId);
  const retry = useRetrySource(notebookId);
  const pending = source.status === "queued" || source.status === "processing";

  return (
    <li className="group rounded-lg px-2 py-2 hover:bg-background">
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={source.status === "ready" && selected}
          disabled={source.status !== "ready"}
          onChange={onToggle}
          aria-label={`Use ${source.title} in chat`}
          className="accent-accent"
        />
        <span className="rounded bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted group-hover:bg-card">
          {KIND_LABEL[source.kind]}
        </span>
        {source.status === "ready" ? (
          <button
            onClick={onOpen}
            title={`Open ${source.filename ?? source.title}`}
            className="min-w-0 flex-1 truncate text-left text-sm hover:text-accent hover:underline"
          >
            {source.title}
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-sm" title={source.filename ?? source.title}>
            {source.title}
          </span>
        )}
        <button
          onClick={() => {
            if (window.confirm(`Remove "${source.title}" from this notebook?`)) {
              remove.mutate(source.id);
            }
          }}
          disabled={remove.isPending}
          className="text-xs text-muted opacity-0 hover:text-danger focus:opacity-100 group-hover:opacity-100"
          aria-label={`Remove ${source.title}`}
        >
          ✕
        </button>
      </div>

      <div className="mt-1 pl-6">
        {pending && (
          <>
            <p className="text-xs text-muted">
              {source.status === "queued"
                ? (source.stage ?? "Waiting to be processed…")
                : `${source.stage ?? "Processing"} · ${source.progress}%`}
            </p>
            <div className="mt-1 h-1 overflow-hidden rounded bg-background">
              <div
                className="h-full bg-accent transition-[width] duration-500"
                style={{ width: `${Math.max(source.progress, 3)}%` }}
              />
            </div>
          </>
        )}
        {source.status === "ready" && <p className="text-xs text-muted">{readySummary(source)}</p>}
        {source.status === "failed" && (
          <p className="text-xs text-danger">
            {source.error ?? "Processing failed."}{" "}
            <button
              onClick={() => retry.mutate(source.id)}
              disabled={retry.isPending}
              className="font-medium text-accent hover:underline"
            >
              Retry
            </button>
          </p>
        )}
        {(remove.error ?? retry.error) && (
          <p className="text-xs text-danger">{(remove.error ?? retry.error)?.message}</p>
        )}
      </div>
    </li>
  );
}

function readySummary(source: Source): string {
  const parts = [];
  if (source.page_count) parts.push(`${source.page_count} page${source.page_count === 1 ? "" : "s"}`);
  parts.push(`${source.chunk_count} passage${source.chunk_count === 1 ? "" : "s"}`);
  if (source.ocr_page_count) parts.push(`OCR on ${source.ocr_page_count}`);
  return parts.join(" · ");
}

function PasteTextDialog({
  ref,
  notebookId,
}: {
  ref: React.Ref<HTMLDialogElement>;
  notebookId: string;
}) {
  const add = useAddTextSource(notebookId);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    add.mutate(
      { title: String(data.get("title")), text: String(data.get("text")) },
      {
        onSuccess: () => {
          form.reset();
          form.closest("dialog")?.close();
        },
      },
    );
  }

  return (
    <dialog
      ref={ref}
      className="m-auto w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-border bg-card p-0 text-foreground backdrop:bg-black/40"
    >
      <form onSubmit={onSubmit} className="space-y-3 p-5">
        <h2 className="font-medium">Paste text</h2>
        <input
          name="title"
          required
          maxLength={300}
          placeholder="Title, e.g. Meeting notes"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <textarea
          name="text"
          required
          rows={12}
          placeholder="Paste the text here…"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
        />
        {add.error && <p className="text-sm text-danger">{add.error.message}</p>}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={(e) => e.currentTarget.closest("dialog")?.close()}
            className="rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-background"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-60"
          >
            {add.isPending ? "Adding…" : "Add source"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
