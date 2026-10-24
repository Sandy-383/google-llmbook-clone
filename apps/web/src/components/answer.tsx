"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Passage } from "@/lib/api";
import { claimsByCitation } from "@/lib/text-match";

// Same rule as the server: [1], [1, 2]; at most three digits so "[2017]" stays text.
const CITATION = /\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g;

/** Turn [n] markers into links the Markdown renderer hands to <CitationChip>. Numbers that
 * don't match a passage are dropped (the server does the same when it saves the answer). */
function linkCitations(text: string, count: number): string {
  return text.replace(CITATION, (_, numbers: string) =>
    numbers
      .split(",")
      .map((n) => Number.parseInt(n.trim(), 10))
      .filter((n) => n >= 1 && n <= count)
      .map((n) => `[${n}](#cite-${n})`)
      .join(""),
  );
}

export function passageLabel(p: Passage): string {
  if (p.page_start === null) return p.heading ? `${p.source_title} › ${p.heading}` : p.source_title;
  const pages =
    p.page_start === p.page_end ? `p. ${p.page_start}` : `pp. ${p.page_start}–${p.page_end}`;
  return `${p.source_title}, ${pages}`;
}

export type ShowInSource = (passage: Passage, claims: string[]) => void;

export function Answer({
  text,
  passages,
  onShowInSource,
}: {
  text: string;
  passages: Passage[];
  onShowInSource?: ShowInSource;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const opened = passages.find((p) => p.number === open);
  // The answer's own sentences behind each citation: the viewer highlights what supports them.
  const claims = useMemo(() => claimsByCitation(text), [text]);

  function cite(n: number) {
    const next = open === n ? null : n;
    setOpen(next);
    const passage = passages.find((p) => p.number === n);
    // On wide screens the viewer sits beside the chat, so open it straight away.
    if (next && passage && onShowInSource && window.matchMedia("(min-width: 1024px)").matches) {
      onShowInSource(passage, claims.get(n) ?? []);
    }
  }

  return (
    <div>
      <div className="answer text-sm leading-relaxed">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              const citation = href?.match(/^#cite-(\d+)$/);
              if (citation) {
                const n = Number(citation[1]);
                const passage = passages.find((p) => p.number === n);
                return (
                  <button
                    type="button"
                    onClick={() => cite(n)}
                    title={passage ? passageLabel(passage) : undefined}
                    aria-label={`Source ${n}${passage ? `: ${passageLabel(passage)}` : ""}`}
                    aria-expanded={open === n}
                    className={`mx-0.5 inline-flex h-4.5 min-w-4.5 -translate-y-px items-center justify-center rounded-full px-1 align-middle text-[10px] font-semibold transition ${
                      open === n
                        ? "bg-accent text-accent-foreground"
                        : "bg-accent/15 text-accent hover:bg-accent/30"
                    }`}
                  >
                    {n}
                  </button>
                );
              }
              return (
                <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="underline">
                  {children}
                </a>
              );
            },
          }}
        >
          {linkCitations(text, passages.length)}
        </ReactMarkdown>
      </div>

      {opened && (
        <figure className="mt-3 rounded-xl border border-border bg-background p-3">
          <figcaption className="mb-2 flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate font-medium">
              [{opened.number}] {passageLabel(opened)}
            </span>
            <span className="flex shrink-0 gap-3">
              {onShowInSource && (
                <button
                  onClick={() => onShowInSource(opened, claims.get(opened.number) ?? [])}
                  className="text-accent hover:underline"
                >
                  Show in document
                </button>
              )}
              <button onClick={() => setOpen(null)} className="text-muted hover:text-foreground">
                Close
              </button>
            </span>
          </figcaption>
          <blockquote className="max-h-56 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted">
            {opened.text}
          </blockquote>
        </figure>
      )}
    </div>
  );
}
