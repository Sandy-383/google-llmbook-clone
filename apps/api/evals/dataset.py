"""Questions with known answers over a fixed set of documents.

Each case says what evidence must be retrieved (text that has to appear in a returned passage)
and what a correct answer must contain. Unanswerable cases check that LLMBook says so instead
of answering from the model's own knowledge.
"""

from dataclasses import dataclass, field

ATTENTION_URL = "https://arxiv.org/pdf/1706.03762v7"
ATTENTION = "attention.pdf"  # "Attention Is All You Need" (Vaswani et al.), 15 pages
HANDBOOK = "handbook.pdf"  # generated: tests.factories.HANDBOOK_PAGES
POLICY = "policy.docx"  # generated: tests.factories.make_docx()


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    # Every string must appear (case-insensitive) in one retrieved passage.
    evidence: list[str] = field(default_factory=list)
    # Every group must be matched by at least one alternative in the answer.
    expect: list[list[str]] = field(default_factory=list)
    # Any of these in the answer means it's wrong.
    forbid: list[str] = field(default_factory=list)
    unanswerable: bool = False
    # Asked right after this case, in the same conversation (tests follow-up rewriting).
    follow_up_of: str | None = None


CASES = [
    # --- "Attention Is All You Need" ---
    Case(
        "optimizer",
        "What optimizer was used to train the Transformer?",
        ["Adam optimizer"],
        [["Adam"]],
    ),
    Case(
        "optimizer-betas",
        "What values did they use for its beta parameters?",
        ["β2 = 0.98"],
        [["0.9"], ["0.98"]],
        follow_up_of="optimizer",
    ),
    Case(
        "lr-schedule",
        "How was the learning rate changed during training?",
        ["warmup_steps = 4000"],
        [["warmup", "warm-up", "warm up"], ["4000", "4,000"]],
    ),
    Case(
        "heads",
        "How many attention heads does the model use?",
        ["parallel attention layers"],
        [["8", "eight"]],
    ),
    Case(
        "ffn",
        "What is the inner dimensionality of the feed-forward layers?",
        ["2048"],
        [["2048", "2,048"]],
    ),
    Case(
        "scaling",
        "Why are the dot products scaled by the square root of d_k?",
        ["extremely small gradients"],
        [["gradient"]],
    ),
    Case(
        "bleu-en-de",
        "What BLEU score did the big Transformer get on English-to-German?",
        ["28.4"],
        [["28.4"]],
    ),
    Case(
        "hardware",
        "What hardware were the models trained on, and for how long?",
        ["8 NVIDIA P100"],
        [["P100"], ["12 hours", "twelve hours", "3.5 days"]],
    ),
    Case(
        "regularization",
        "Which regularization techniques were used during training?",
        ["Residual Dropout"],
        [["dropout"], ["label smoothing"]],
    ),
    Case(
        "positional",
        "How are positional encodings computed?",
        ["sine and cosine"],
        [["sin"], ["cos"]],
    ),
    Case(
        "en-fr-data",
        "How large was the English-French training dataset?",
        ["36M sentences"],
        [["36M", "36 million"]],
    ),
    Case("beam", "What beam size was used for decoding?", ["beam size of 4"], [["4", "four"]]),
    # --- Handbook (PDF) and policy (Word) ---
    Case(
        "leave", "How many days of annual leave do full-time employees get?", ["27 days"], [["27"]]
    ),
    Case(
        "carry-over",
        "How many unused leave days can be carried over?",
        ["carried over"],
        [["5", "five"]],
    ),
    Case(
        "business-class",
        "When is business class allowed for travel?",
        ["Business class"],
        [["director"]],
    ),
    Case(
        "expense-deadline",
        "How long do I have to submit an expense claim?",
        ["30 days"],
        [["30 days", "thirty days"]],
    ),
    Case(
        "receipt",
        "I spent 30 euros on lunch during a trip. Do I need a receipt?",
        ["25 euros"],
        [["receipt"], ["yes", "required", "need"]],
        forbid=["not required", "no receipt is", "don't need", "do not need"],
    ),
    Case(
        "laptop",
        "What are the laptop security rules?",
        ["full-disk encryption"],
        [["encrypt"], ["5 minutes", "five minutes"]],
    ),
    # --- Not in the sources: must not be answered from memory ---
    Case("stock-options", "What is the company's policy on stock options?", unanswerable=True),
    Case("world-cup", "Who won the 2018 FIFA World Cup?", unanswerable=True, forbid=["France"]),
    Case("gpt3", "How many parameters does GPT-3 have?", unanswerable=True, forbid=["175"]),
]

NOT_FOUND_PATTERNS = [
    "couldn't find",
    "could not find",
    "not mentioned",
    "not contain",
    "doesn't contain",
    "does not contain",
    "no information",
    "don't provide",
    "do not provide",
    "does not provide",
    "doesn't provide",
    "not provided",
]
