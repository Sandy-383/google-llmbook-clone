/**
 * Finding cited text inside a rendered document.
 *
 * The server extracted passages with PDFium; the browser renders the PDF with pdf.js. The two
 * split text differently (spacing, hyphenation, ligatures, line breaks), so matching compares
 * only letters and digits after Unicode normalisation, and keeps a map back to the exact
 * characters of each text item so they can be highlighted.
 */

/** [start, end) offsets within one text item's string. */
export type Range = [number, number];
export type ItemRanges = Map<number, Range[]>;

const CITATION = /\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g;
const LEADING_CITATIONS = /^((?:\[\d{1,3}(?:\s*,\s*\d{1,3})*\]\s*)+)/;
// Letters, digits and combining marks: Indic scripts (Kannada, Hindi…) need the marks, e.g. the
// virama in "ಕನ್ನಡ", or words fall apart.
const WORD = /[\p{L}\p{M}\p{N}]+/gu;
const ALNUM = /[\p{L}\p{M}\p{N}]/u;

const STOPWORDS = new Set(
  (
    "a an the and or but of to in on for with by from at as is are was were be been being it its " +
    "this that these those which who whom what when where how why do does did can could should " +
    "would will may might must not no than then there their they them we our you your i he she " +
    "his her also into about over such each per"
  ).split(" "),
);

function fold(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}

/** Letters and digits only, normalised: what both extractors agree on. */
export function alnum(text: string): string {
  return (fold(text).match(WORD) ?? []).join("");
}

type Stream = { chars: string; item: number[]; start: number[]; end: number[] };

function toStream(items: string[]): Stream {
  const parts: string[] = [];
  const item: number[] = [];
  const start: number[] = [];
  const end: number[] = [];
  items.forEach((str, index) => {
    let offset = 0;
    for (const ch of str) {
      // One source character can normalise to several (the "ﬁ" ligature becomes "fi").
      for (const c of fold(ch)) {
        if (ALNUM.test(c)) {
          parts.push(c);
          item.push(index);
          start.push(offset);
          end.push(offset + ch.length);
        }
      }
      offset += ch.length;
    }
  });
  return { chars: parts.join(""), item, start, end };
}

/** Where `needle` occurs in `hay`, tolerating small differences in the middle or a sentence that
 * continues on another page. */
function locate(hay: string, needle: string): Range[] {
  if (needle.length < 8) return [];
  const exact = hay.indexOf(needle);
  if (exact >= 0) return [[exact, exact + needle.length]];

  const k = Math.min(40, Math.floor(needle.length / 3));
  if (k >= 12) {
    const head = hay.indexOf(needle.slice(0, k));
    const tail = head >= 0 ? hay.indexOf(needle.slice(-k), head) : -1;
    if (head >= 0 && tail >= 0 && tail + k - head <= needle.length * 1.3) return [[head, tail + k]];
  }

  // Fall back to pieces, so the visible part of a split sentence is still marked.
  const found: Range[] = [];
  const piece = 24;
  for (let s = 0; s + piece <= needle.length; s += piece) {
    const at = hay.indexOf(needle.slice(s, s + piece));
    if (at >= 0) found.push([at, at + piece]);
  }
  return found;
}

/** Character ranges to highlight in each text item, for every target sentence found. */
export function findRanges(items: string[], targets: string[]): ItemRanges {
  const stream = toStream(items);
  const marked = new Uint8Array(stream.chars.length);
  for (const target of targets) {
    for (const [a, b] of locate(stream.chars, alnum(target))) marked.fill(1, a, b);
  }

  const ranges: ItemRanges = new Map();
  let previous = -2;
  for (let p = 0; p < marked.length; p++) {
    if (!marked[p]) continue;
    const index = stream.item[p];
    const list = ranges.get(index) ?? [];
    const last = list.at(-1);
    // Consecutive matched characters in the same item form one range, spaces between included.
    if (last && previous === p - 1 && stream.item[previous] === index) last[1] = stream.end[p];
    else list.push([stream.start[p], stream.end[p]]);
    ranges.set(index, list);
    previous = p;
  }
  return ranges;
}

export function markedLength(ranges: ItemRanges): number {
  let total = 0;
  for (const list of ranges.values()) for (const [a, b] of list) total += b - a;
  return total;
}

/** Text split into plain and highlighted segments. */
export function segments(text: string, ranges: Range[] = []): { text: string; marked: boolean }[] {
  const out: { text: string; marked: boolean }[] = [];
  let at = 0;
  for (const [a, b] of [...ranges].sort((x, y) => x[0] - y[0])) {
    if (a > at) out.push({ text: text.slice(at, a), marked: false });
    if (b > Math.max(a, at)) out.push({ text: text.slice(Math.max(a, at), b), marked: true });
    at = Math.max(at, b);
  }
  if (at < text.length) out.push({ text: text.slice(at), marked: false });
  return out;
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** HTML for one pdf.js text item, with matches wrapped in <mark>. */
export function highlightHtml(text: string, ranges?: Range[]): string {
  return segments(text, ranges)
    .map(({ text: t, marked }) => {
      const escaped = t.replace(/[&<>"']/g, (c) => ESCAPES[c]);
      return marked ? `<mark class="cite-mark">${escaped}</mark>` : escaped;
    })
    .join("");
}

export function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+|\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function terms(text: string): Set<string> {
  return new Set(
    (fold(text).match(WORD) ?? []).filter(
      (t) => !STOPWORDS.has(t) && (t.length > 1 || /\d/.test(t)),
    ),
  );
}

/**
 * The sentences of a cited passage that support the answer's claims, strongest first, so the
 * viewer can highlight a sentence or two instead of the whole passage. Each claim (an answer
 * sentence citing this passage) gets its own best match: one passage often backs several
 * different statements. Without any overlap (or without claims) the whole passage is returned.
 */
export function supportingSentences(passage: string, claims: string[], perClaim = 2): string[] {
  const sentences = splitSentences(passage);
  const sentenceTerms = sentences.map(terms);
  // A word found in many sentences of the passage ("model", "Transformer") says little about
  // which sentence is meant; a rare one ("Adam") says a lot.
  const weight = (t: string) => {
    const df = sentenceTerms.filter((s) => s.has(t)).length;
    return Math.log(1 + sentences.length / Math.max(df, 1));
  };

  const picks: string[][] = [];
  for (const claim of claims) {
    const wanted = terms(claim);
    if (wanted.size === 0) continue;
    const scored = sentences.map((sentence, index) => {
      let score = 0;
      for (const t of wanted) if (sentenceTerms[index].has(t)) score += weight(t);
      // Favour sentences that cover the claim, without simply favouring long sentences.
      return { sentence, score: score / Math.sqrt(Math.max(sentenceTerms[index].size, 1)) };
    });
    const best = Math.max(...scored.map((s) => s.score));
    if (best === 0) continue;
    picks.push(
      scored
        .filter((s) => s.score >= best * 0.6)
        .sort((a, b) => b.score - a.score)
        .slice(0, perClaim)
        .map((s) => s.sentence),
    );
  }
  if (picks.length === 0) return sentences;

  // Interleave: every claim's best match first, then the runners-up; no duplicates.
  const ordered: string[] = [];
  for (let rank = 0; rank < perClaim; rank++) {
    for (const pick of picks) {
      if (pick[rank] && !ordered.includes(pick[rank])) ordered.push(pick[rank]);
    }
  }
  return ordered;
}

/**
 * Of the candidate pages (a passage can span a few), the one holding the strongest supporting
 * sentence, with every target on that page marked. `targets` must be strongest first.
 */
export function locateTargets(
  pages: { page: number; items: string[] }[],
  targets: string[],
): { page: number; ranges: ItemRanges } | null {
  for (const target of targets) {
    for (const { page, items } of pages) {
      if (markedLength(findRanges(items, [target])) > 0) {
        return { page, ranges: findRanges(items, targets) };
      }
    }
  }
  return null;
}

/** For each citation number in an answer, the sentences that cite it. */
export function claimsByCitation(answer: string): Map<number, string[]> {
  const claims = new Map<number, string[]>();
  const add = (n: number, text: string) => {
    if (text) claims.set(n, [...(claims.get(n) ?? []), text]);
  };
  let previous = "";
  for (const raw of answer.split(/(?<=[.!?])\s+|\n+/)) {
    let sentence = raw.trim();
    // "...a fact. [1] Next sentence": a marker at the start belongs to the previous sentence.
    const leading = sentence.match(LEADING_CITATIONS);
    if (leading) {
      for (const n of numbers(leading[1])) add(n, previous);
      sentence = sentence.slice(leading[1].length);
    }
    const text = sentence
      .replace(CITATION, "")
      .replace(/\s+([.,;:!?])/g, "$1")
      .trim();
    for (const n of numbers(sentence)) add(n, text);
    if (text) previous = text;
  }
  return claims;
}

function numbers(text: string): number[] {
  return [...text.matchAll(CITATION)].flatMap((m) =>
    m[1].split(",").map((n) => Number.parseInt(n.trim(), 10)),
  );
}
