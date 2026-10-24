export type User = {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
};

export type Notebook = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type SourceKind = "pdf" | "docx" | "text" | "markdown";
export type SourceStatus = "queued" | "processing" | "ready" | "failed";

export type SourceGuide = {
  summary: string;
  topics: string[];
  questions: string[];
  error: string | null;
};

export type Source = {
  id: string;
  kind: SourceKind;
  title: string;
  filename: string | null;
  size_bytes: number;
  status: SourceStatus;
  stage: string | null;
  progress: number;
  error: string | null;
  page_count: number | null;
  ocr_page_count: number;
  chunk_count: number;
  /** null: never requested (sources added before guides existed). */
  guide_status: "pending" | "ready" | "failed" | null;
  guide: SourceGuide | null;
  created_at: string;
  updated_at: string;
};

export type StudioKind = "study_guide" | "faq" | "briefing" | "timeline";
export type NoteKind = "manual" | "answer" | StudioKind;

export type Note = {
  id: string;
  kind: NoteKind;
  title: string;
  content: string;
  status: "ready" | "generating" | "failed";
  error: string | null;
  passages: Passage[] | null;
  meta: { source_ids?: string[]; sources?: string[]; omitted?: string[] } | null;
  created_at: string;
  updated_at: string;
};

export type Passage = {
  number: number;
  source_id: string;
  source_title: string;
  page_start: number | null;
  page_end: number | null;
  heading: string | null;
  text: string;
  similarity: number | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "complete" | "stopped" | "error";
  passages: Passage[] | null;
  meta: { error?: string | null; search_query?: string; not_found?: boolean } | null;
  created_at: string;
};

export type SourceContent = {
  sections: { heading: string | null; page: number | null; text: string }[];
};

/** Mirrors the server's limits, so obviously bad files are rejected before uploading. */
export const UPLOAD_LIMIT_MB = 50;
export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".markdown"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ValidationIssue = { loc: (string | number)[]; msg: string };

export async function errorMessage(res: Response): Promise<string> {
  try {
    const body: { detail?: string | ValidationIssue[] } = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ");
    }
  } catch {
    // Non-JSON error body (e.g. a proxy error page); fall through to the generic message.
  }
  return GENERIC_ERRORS[res.status] ?? `Request failed (${res.status})`;
}

const GENERIC_ERRORS: Record<number, string> = {
  413: `That file is too large. The limit is ${UPLOAD_LIMIT_MB} MB.`,
  429: "Too many requests. Try again in a little while.",
  502: "The server isn't responding. Try again in a moment.",
  503: "The server isn't responding. Try again in a moment.",
  504: "The server took too long to respond. Try again in a moment.",
};

/** Calls the FastAPI backend, which is always served under /api on the same origin. */
export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    ...rest,
    credentials: "same-origin",
    headers: json === undefined ? headers : { "content-type": "application/json", ...headers },
    body: json === undefined ? rest.body : JSON.stringify(json),
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}
