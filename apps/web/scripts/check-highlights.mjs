// Checks citation highlighting against a real PDF, without a browser: for every cited passage in
// an eval run (apps/api/evals), extract the cited pages with pdf.js exactly as the viewer does,
// pick the supporting sentences and report whether the highlight covers the case's evidence.
//
//   node scripts/check-highlights.mjs ../api/data/eval-cache/attention.pdf ../api/data/evals/eval-….json
import { readFileSync } from "node:fs";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import {
  alnum,
  claimsByCitation,
  locateTargets,
  markedLength,
  supportingSentences,
} from "../src/lib/text-match.ts";

const [pdfPath, evalPath] = process.argv.slice(2);
const pdf = await getDocument({ data: new Uint8Array(readFileSync(pdfPath)), isEvalSupported: false })
  .promise;
const pages = new Map();
async function pageItems(n) {
  if (!pages.has(n)) {
    const content = await (await pdf.getPage(n)).getTextContent();
    pages.set(n, content.items.map((item) => ("str" in item ? item.str : "")));
  }
  return pages.get(n);
}

const words = (s) => new Set(s.normalize("NFKC").toLowerCase().match(/[\p{L}\p{M}\p{N}]+/gu) ?? []);

let checked = 0;
let highlightedSomething = 0;
let stated = 0;
let highlightedEvidence = 0;
for (const result of JSON.parse(readFileSync(evalPath, "utf8"))) {
  if (result.unanswerable || !result.answer) continue;
  const claims = claimsByCitation(result.answer);
  for (const n of result.cited ?? []) {
    const passage = result.passages.find((p) => p.number === n);
    if (!passage || passage.page_start === null || passage.source_title !== "attention") continue;
    checked++;
    const targets = supportingSentences(passage.text, claims.get(n) ?? []);
    const candidates = [];
    for (let page = passage.page_start; page <= passage.page_end; page++) {
      candidates.push({ page, items: await pageItems(page) });
    }
    // Same choice as the viewer: the page of the strongest supporting sentence.
    const found = locateTargets(candidates, targets);
    let best = { page: passage.page_start, marked: 0, text: "" };
    if (found) {
      const items = await pageItems(found.page);
      const text = [...found.ranges]
        .flatMap(([i, rs]) => rs.map(([a, b]) => items[i].slice(a, b)))
        .join(" ");
      best = { page: found.page, marked: markedLength(found.ranges), text };
    }
    if (!result.evidence) throw new Error("Eval results lack `evidence`; rerun evals/run.py.");
    const coversEvidence = result.evidence.every((e) => alnum(best.text).includes(alnum(e)));
    // Only expect the evidence highlighted if an answer sentence citing this passage states it;
    // a citation backing a different statement should highlight that statement instead.
    const claimWords = words((claims.get(n) ?? []).join(" "));
    const states = result.evidence.every((e) => [...words(e)].every((w) => claimWords.has(w)));
    if (best.marked) highlightedSomething++;
    if (states) stated++;
    if (states && coversEvidence) highlightedEvidence++;
    const verdict = coversEvidence
      ? "evidence highlighted"
      : states
        ? "EVIDENCE NOT HIGHLIGHTED"
        : "(cites it for other text)";
    console.log(
      `${result.id.padEnd(16)} [${n}] p.${String(best.page).padEnd(3)} ` +
        `${best.marked ? `${String(best.marked).padStart(4)} chars` : "  no match"}  ` +
        `${verdict.padEnd(26)}"${best.text.slice(0, 64)}"`,
    );
  }
}
console.log(
  `\n${checked} citations into the PDF: ${highlightedSomething} highlighted; of the ${stated} whose ` +
    `answer sentence states the evidence, the highlight covers it in ${highlightedEvidence}`,
);
