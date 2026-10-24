"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef } from "react";
import { passageLabel } from "@/components/answer";
import type { Passage, Source } from "@/lib/api";
import { useRegenerateGuide, useSourceContent } from "@/lib/queries";
import { findRanges, segments, supportingSentences } from "@/lib/text-match";

// pdf.js needs the browser (canvas, workers), so it is never rendered on the server.
const PdfViewer = dynamic(() => import("@/components/pdf-viewer"), {
  ssr: false,
  loading: () => <p className="p-6 text-center text-sm text-muted">Loading viewer…</p>,
});

export type ViewerTarget = {
  sourceId: string;
  /** Set when opened from a citation: the passage to highlight and the answer sentences it
   * supports. */
  passage?: Passage;
  claims?: string[];
};

export function SourceViewer({
  notebookId,
  source,
  target,
  onClose,
  onAsk,
}: {
  notebookId: string;
  source: Source | undefined;
  target: ViewerTarget;
  onClose: () => void;
  onAsk: (question: string) => void;
}) {
  const { passage } = target;
  const targets = useMemo(
    () => (passage ? supportingSentences(passage.text, target.claims ?? []) : []),
    [passage, target.claims],
  );
  const fileUrl = `/api/notebooks/${notebookId}/sources/${target.sourceId}/file`;
  const title = source?.title ?? passage?.source_title ?? "Source";

  return (
    <section
      id="source-viewer"
      className="flex min-h-[70vh] flex-col overflow-hidden rounded-2xl border border-border bg-card lg:min-h-0"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <button
          onClick={onClose}
          className="rounded-md px-2 py-1 text-xs text-muted hover:bg-background hover:text-foreground"
        >
          ← Sources
        </button>
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium" title={title}>
          {title}
        </h2>
        {source && (
          <a
            href={fileUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 rounded-md px-2 py-1 text-xs text-accent hover:bg-background"
          >
            Open original ↗
          </a>
        )}
      </div>

      {source?.status === "ready" && (
        <SourceGuide
          notebookId={notebookId}
          source={source}
          collapsed={Boolean(passage)}
          onAsk={onAsk}
        />
      )}

      {passage && (
        <details open className="border-b border-border bg-background/60 px-3 py-2 text-xs">
          <summary className="cursor-pointer font-medium">
            Cited passage [{passage.number}] · {passageLabel(passage)}
          </summary>
          <p className="mt-1.5 max-h-28 overflow-y-auto whitespace-pre-wrap leading-relaxed text-muted">
            {passage.text}
          </p>
        </details>
      )}

      {!source ? (
        <p className="p-6 text-center text-sm text-muted">
          This source has been removed from the notebook. The cited passage is shown above.
        </p>
      ) : source.status !== "ready" ? (
        <p className="p-6 text-center text-sm text-muted">This source is still being processed.</p>
      ) : source.kind === "pdf" ? (
        <PdfViewer
          url={fileUrl}
          targets={targets}
          pages={
            passage?.page_start
              ? { start: passage.page_start, end: passage.page_end ?? passage.page_start }
              : null
          }
        />
      ) : (
        <TextViewer notebookId={notebookId} sourceId={source.id} targets={targets} />
      )}
    </section>
  );
}

/** Summary, key topics and suggested questions, written in the background after processing.
 * Collapsed when the viewer was opened from a citation, where the passage matters more. */
function SourceGuide({
  notebookId,
  source,
  collapsed,
  onAsk,
}: {
  notebookId: string;
  source: Source;
  collapsed: boolean;
  onAsk: (question: string) => void;
}) {
  const regenerate = useRegenerateGuide(notebookId);
  const { guide_status: status, guide } = source;
  const action = "font-medium text-accent hover:underline disabled:opacity-50";

  return (
    <details key={String(collapsed)} open={!collapsed} className="border-b border-border px-3 py-2 text-xs">
      <summary className="cursor-pointer font-medium">Source guide</summary>
      {status === "pending" && (
        <p className="mt-1.5 animate-pulse text-muted">
          Writing a summary and suggested questions… This takes a minute or two.
        </p>
      )}
      {status === "failed" && (
        <p className="mt-1.5 text-danger">
          {guide?.error ?? "The guide couldn't be written."}{" "}
          <button onClick={() => regenerate.mutate(source.id)} disabled={regenerate.isPending} className={action}>
            Try again
          </button>
        </p>
      )}
      {status === null && (
        <button onClick={() => regenerate.mutate(source.id)} disabled={regenerate.isPending} className={`mt-1.5 ${action}`}>
          Write a summary and suggested questions
        </button>
      )}
      {status === "ready" && guide && (
        <div className="mt-1.5 space-y-2.5">
          <p className="text-sm leading-relaxed">{guide.summary}</p>
          {guide.topics.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {guide.topics.map((topic) => (
                <li key={topic} className="rounded-full bg-background px-2 py-0.5 text-muted">
                  {topic}
                </li>
              ))}
            </ul>
          )}
          {guide.questions.length > 0 && (
            <div className="space-y-1">
              {guide.questions.map((question) => (
                <button
                  key={question}
                  onClick={() => onAsk(question)}
                  className="block w-full rounded-lg border border-border px-2.5 py-1.5 text-left text-xs hover:border-accent"
                >
                  {question}
                </button>
              ))}
            </div>
          )}
          <button onClick={() => regenerate.mutate(source.id)} disabled={regenerate.isPending} className={`text-muted ${action}`}>
            Rewrite guide
          </button>
        </div>
      )}
      {regenerate.error && <p className="mt-1 text-danger">{regenerate.error.message}</p>}
    </details>
  );
}

/** Word, text and Markdown sources: their extracted text by section, with the citation marked. */
function TextViewer({
  notebookId,
  sourceId,
  targets,
}: {
  notebookId: string;
  sourceId: string;
  targets: string[];
}) {
  const content = useSourceContent(notebookId, sourceId);
  const frame = useRef<HTMLDivElement>(null);
  const marked = useMemo(
    () => content.data?.sections.map((s) => findRanges([s.text], targets).get(0) ?? []) ?? [],
    [content.data, targets],
  );

  useEffect(() => {
    frame.current?.querySelector("mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [marked]);

  if (content.isPending) return <p className="p-6 text-center text-sm text-muted">Loading…</p>;
  if (content.isError) return <p className="p-6 text-center text-sm text-danger">{content.error.message}</p>;

  return (
    <div ref={frame} className="text-viewer min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
      {content.data.sections.map((section, i) => (
        <div key={i}>
          {section.heading && (
            <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {section.heading}
            </h3>
          )}
          <p className="whitespace-pre-wrap leading-relaxed">
            {segments(section.text, marked[i]).map((part, j) =>
              part.marked ? <mark key={j}>{part.text}</mark> : <span key={j}>{part.text}</span>,
            )}
          </p>
        </div>
      ))}
    </div>
  );
}
