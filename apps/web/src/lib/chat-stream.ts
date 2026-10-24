import { ApiError, errorMessage, type ChatMessage, type Passage } from "./api";

export type ChatEvent =
  | { event: "question"; data: { message: ChatMessage } }
  | { event: "searching"; data: { query: string } }
  | { event: "sources"; data: { passages: Passage[] } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: { message: ChatMessage } }
  | { event: "error"; data: { detail: string; message: ChatMessage } };

/**
 * POSTs a question and reads the Server-Sent Events answer stream. (EventSource can't POST,
 * so the stream is parsed here.) Aborting `signal` closes the connection, which stops
 * generation on the server; the partial answer is kept there as "stopped".
 */
export async function streamChat(
  notebookId: string,
  body: { message: string; source_ids: string[] },
  { signal, onEvent }: { signal: AbortSignal; onEvent: (e: ChatEvent) => void },
): Promise<void> {
  const res = await fetch(`/api/notebooks/${notebookId}/chat`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, await errorMessage(res));

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (event && data) onEvent({ event, data: JSON.parse(data) } as ChatEvent);
    }
  }
}
