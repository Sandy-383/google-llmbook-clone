"use client";

import type { PDFDocumentProxy } from "pdfjs-dist";
import type { TextContent } from "pdfjs-dist/types/src/display/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { findRanges, highlightHtml, locateTargets, type ItemRanges } from "@/lib/text-match";

// Must be set in the module that renders <Document> (see the react-pdf README). The worker and
// the assets below are copied into public/pdfjs at build time, so nothing loads from a CDN.
pdfjs.GlobalWorkerOptions.workerSrc = `/pdfjs/pdf.worker.min.mjs?v=${pdfjs.version}`;

// Module-level so its identity is stable: react-pdf reloads the document when `options` changes.
const OPTIONS = {
  cMapUrl: "/pdfjs/cmaps/",
  standardFontDataUrl: "/pdfjs/standard_fonts/",
  wasmUrl: "/pdfjs/wasm/",
  iccUrl: "/pdfjs/iccs/",
  // Defence in depth for uploaded files: never compile font code from a PDF.
  isEvalSupported: false,
};

const strings = (items: TextContent["items"]) =>
  items.map((item) => ("str" in item ? item.str : ""));

/** Renders one page at a time, opened on the cited sentence, with it highlighted. */
export default function PdfViewer({
  url,
  targets,
  pages: hint,
}: {
  url: string;
  /** Sentences to highlight, strongest first. */
  targets: string[];
  /** Pages the cited passage spans; the viewer opens on whichever holds the best match. */
  pages: { start: number; end: number } | null;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const scrolledTo = useRef("");
  const [width, setWidth] = useState<number>();
  const [zoom, setZoom] = useState(1);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(hint?.start ?? 1);
  const [ranges, setRanges] = useState<{ page: number; map: ItemRanges }>({
    page: 0,
    map: new Map(),
  });
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // A passage can span a few pages: open the one holding the strongest supporting sentence.
  useEffect(() => {
    if (!pdf) return;
    let cancelled = false;
    const start = Math.min(hint?.start ?? 1, pdf.numPages);
    const end = Math.min(Math.max(hint?.end ?? start, start), pdf.numPages);
    void (async () => {
      const candidates = [];
      for (let p = start; p <= end && targets.length; p++) {
        const content = await (await pdf.getPage(p)).getTextContent();
        candidates.push({ page: p, items: strings(content.items) });
      }
      if (cancelled) return;
      const found = targets.length ? locateTargets(candidates, targets) : null;
      setPage(found?.page ?? start);
      setNotFound(targets.length > 0 && !found);
    })();
    return () => {
      cancelled = true;
    };
  }, [pdf, targets, hint?.start, hint?.end]);

  const onText = useCallback(
    (content: TextContent) =>
      setRanges({
        page,
        map: targets.length ? findRanges(strings(content.items), targets) : new Map(),
      }),
    [page, targets],
  );

  const renderText = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) =>
      highlightHtml(str, ranges.page === page ? ranges.map.get(itemIndex) : undefined),
    [ranges, page],
  );

  // Scroll the highlight into view once per citation, not on every re-render (zoom, resize).
  const onTextLayer = useCallback(() => {
    const key = `${page}|${targets.join("|")}`;
    const mark = frame.current?.querySelector("mark.cite-mark");
    if (mark && scrolledTo.current !== key) {
      scrolledTo.current = key;
      mark.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [page, targets]);

  const total = pdf?.numPages ?? 0;
  const go = (p: number) => setPage(Math.min(Math.max(1, p), total || 1));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-xs">
        <div className="flex items-center gap-1">
          <ToolButton label="Previous page" onClick={() => go(page - 1)} disabled={page <= 1}>
            ‹
          </ToolButton>
          <span className="tabular-nums text-muted">
            Page{" "}
            <input
              key={page}
              defaultValue={page}
              inputMode="numeric"
              aria-label="Page number"
              onKeyDown={(e) => e.key === "Enter" && go(Number(e.currentTarget.value) || page)}
              className="w-10 rounded border border-border bg-background px-1 text-center"
            />{" "}
            of {total || "…"}
          </span>
          <ToolButton label="Next page" onClick={() => go(page + 1)} disabled={page >= total}>
            ›
          </ToolButton>
        </div>
        <div className="flex items-center gap-1">
          <ToolButton label="Zoom out" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}>
            −
          </ToolButton>
          <button
            onClick={() => setZoom(1)}
            className="w-12 rounded px-1 py-0.5 tabular-nums text-muted hover:bg-background"
            title="Fit to width"
          >
            {Math.round(zoom * 100)}%
          </button>
          <ToolButton label="Zoom in" onClick={() => setZoom((z) => Math.min(3, z + 0.25))}>
            +
          </ToolButton>
        </div>
      </div>

      {notFound && (
        <p className="border-b border-border bg-background px-3 py-1.5 text-xs text-muted">
          The exact sentence couldn&apos;t be located on the page (scanned pages have no text
          layer). The cited passage is shown above.
        </p>
      )}

      <div ref={frame} className="min-h-0 flex-1 overflow-auto bg-background p-2">
        <Document
          file={url}
          options={OPTIONS}
          onLoadSuccess={setPdf}
          externalLinkTarget="_blank"
          externalLinkRel="noopener noreferrer nofollow"
          loading={<p className="p-6 text-center text-sm text-muted">Loading document…</p>}
          error={<p className="p-6 text-center text-sm text-danger">This PDF couldn&apos;t be displayed.</p>}
        >
          {width && (
            <Page
              pageNumber={page}
              width={width * zoom}
              customTextRenderer={renderText}
              onGetTextSuccess={onText}
              onRenderTextLayerSuccess={onTextLayer}
              loading={<div style={{ height: width * 1.3 }} />}
              className="mx-auto w-fit shadow-sm"
            />
          )}
        </Document>
      </div>
    </div>
  );
}

function ToolButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="h-6 w-6 rounded text-base leading-none text-muted hover:bg-background hover:text-foreground disabled:opacity-30"
    >
      {children}
    </button>
  );
}
