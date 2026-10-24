import { describe, expect, it } from "vitest";
import {
  alnum,
  claimsByCitation,
  findRanges,
  highlightHtml,
  locateTargets,
  segments,
  supportingSentences,
} from "./text-match";

function highlighted(items: string[], targets: string[]): string[] {
  const ranges = findRanges(items, targets);
  return items.flatMap((str, i) => (ranges.get(i) ?? []).map(([a, b]) => str.slice(a, b)));
}

describe("findRanges", () => {
  it("matches across text items despite spacing and hyphenation differences", () => {
    // pdf.js splits lines into items and keeps the hyphen; the server de-hyphenated.
    const items = ["We used the Adam opti-", "mizer with β1 = 0.9,", " β2 = 0.98 and ε = 10−9."];
    const target = "We used the Adam optimizer with β1 = 0.9, β2 = 0.98";
    expect(highlighted(items, [target])).toEqual([
      "We used the Adam opti",
      "mizer with β1 = 0.9",
      "β2 = 0.98",
    ]);
  });

  it("treats ligatures like their letters", () => {
    expect(highlighted(["The ﬁnal layer is ﬂat."], ["The final layer is flat"])).toEqual([
      "The ﬁnal layer is ﬂat",
    ]);
  });

  it("still marks the visible part of a sentence that continues on another page", () => {
    const page = ["Training took 3.5 days on eight P100 GPUs and the big model reached"];
    const target =
      "Training took 3.5 days on eight P100 GPUs and the big model reached 41.8 BLEU on WMT 2014.";
    expect(highlighted(page, [target]).join("")).toContain("3.5 days on eight P100");
  });

  it("finds nothing for text that isn't there", () => {
    expect(findRanges(["Completely unrelated content here."], ["Annual leave is 27 days."]).size).toBe(0);
  });

  it("ignores targets too short to match reliably", () => {
    expect(findRanges(["a b c"], ["a b"]).size).toBe(0);
  });
});

describe("highlightHtml", () => {
  it("wraps matches and escapes everything", () => {
    expect(highlightHtml("x < y & <b>bold</b>", [[4, 5]])).toBe(
      'x &lt; <mark class="cite-mark">y</mark> &amp; &lt;b&gt;bold&lt;/b&gt;',
    );
  });

  it("splits text into plain and marked segments", () => {
    expect(segments("abcdef", [[1, 3]])).toEqual([
      { text: "a", marked: false },
      { text: "bc", marked: true },
      { text: "def", marked: false },
    ]);
  });
});

describe("supportingSentences", () => {
  const passage =
    "5.3 Optimizer We used the Adam optimizer with β1 = 0.9, β2 = 0.98. " +
    "We varied the learning rate over the course of training. " +
    "We used warmup_steps = 4000. 5.4 Regularization We employ three types of regularization.";

  it("picks the sentence that supports the claim", () => {
    expect(supportingSentences(passage, ["They used warmup steps of 4000."])[0]).toBe(
      "We used warmup_steps = 4000.",
    );
  });

  it("finds a match for each claim when one passage backs several statements", () => {
    const found = supportingSentences(passage, [
      "The learning rate was varied over the course of training.",
      "The warmup steps were set to 4000.",
    ]);
    expect(found).toContain("We varied the learning rate over the course of training.");
    expect(found).toContain("We used warmup_steps = 4000.");
  });

  it("prefers distinctive words over ones the passage repeats everywhere", () => {
    const text =
      "The Transformer was trained on eight GPUs. " +
      "The Transformer (big) model trained for English-to-French used dropout 0.1. " +
      "We used the Adam optimizer with β1 = 0.9. " +
      "The Transformer base model was trained for 12 hours.";
    expect(supportingSentences(text, ["The Transformer was trained with the Adam optimizer."])[0]).toBe(
      "We used the Adam optimizer with β1 = 0.9.",
    );
  });

  it("falls back to the whole passage without a usable claim", () => {
    expect(supportingSentences(passage, [])).toHaveLength(4);
    expect(supportingSentences(passage, ["zebra giraffe"])).toHaveLength(4);
  });
});

describe("locateTargets", () => {
  it("opens the page of the strongest sentence, not the one with the most text marked", () => {
    const pages = [
      { page: 7, items: ["We used the Adam optimizer with β1 = 0.9."] },
      { page: 8, items: ["A much longer weaker supporting sentence about training models here."] },
    ];
    const found = locateTargets(pages, [
      "We used the Adam optimizer with β1 = 0.9.",
      "A much longer weaker supporting sentence about training models here.",
    ]);
    expect(found?.page).toBe(7);
  });

  it("returns null when no target is on any page", () => {
    expect(locateTargets([{ page: 1, items: ["nothing relevant"] }], ["Adam optimizer β1 0.9"])).toBeNull();
  });
});

describe("claimsByCitation", () => {
  it("collects the sentences that cite each source", () => {
    const answer =
      "Adam was used [1]. Its betas were 0.9 and 0.98 [1][2].\n\n- Warmup lasted 4000 steps. [3]";
    const claims = claimsByCitation(answer);
    expect(claims.get(1)).toEqual(["Adam was used.", "Its betas were 0.9 and 0.98."]);
    expect(claims.get(2)).toEqual(["Its betas were 0.9 and 0.98."]);
    expect(claims.get(3)).toEqual(["- Warmup lasted 4000 steps."]);
  });
});

describe("alnum", () => {
  it("keeps letters and digits in any script", () => {
    expect(alnum("Café — 27 days, ಕನ್ನಡ!")).toBe("café27daysಕನ್ನಡ");
  });
});
