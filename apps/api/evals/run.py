"""Evaluate retrieval and answer quality against a running LLMBook (`docker compose up`).

    uv run python -m evals.run                    # retrieval + answers
    uv run python -m evals.run --retrieval-only   # search quality only (fast)

Uses a dedicated account (eval@example.com) and a throwaway notebook, deleted afterwards unless
--keep is given. Results are saved under data/evals/.
"""

import argparse
import json
import re
import statistics
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from evals.dataset import (
    ATTENTION,
    ATTENTION_URL,
    CASES,
    HANDBOOK,
    NOT_FOUND_PATTERNS,
    POLICY,
    Case,
)
from tests.factories import HANDBOOK_PAGES, make_docx, make_pdf

EMAIL, PASSWORD = "eval@example.com", "llmbook-eval-password"
CITATION = re.compile(r"\[(\d{1,3})\]")


def normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def documents(cache: Path) -> dict[str, bytes]:
    cache.mkdir(parents=True, exist_ok=True)
    paper = cache / ATTENTION
    if not paper.exists():
        print(f"Downloading {ATTENTION_URL} ...")
        resp = httpx.get(ATTENTION_URL, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        paper.write_bytes(resp.content)
    return {ATTENTION: paper.read_bytes(), HANDBOOK: make_pdf(HANDBOOK_PAGES), POLICY: make_docx()}


def request(http: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Waits out rate limits instead of failing the run."""
    while True:
        resp = http.request(method, url, **kwargs)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        wait = int(resp.headers.get("retry-after", "30"))
        print(f"  rate limited, waiting {wait}s ...")
        time.sleep(wait)


def sign_in(http: httpx.Client) -> None:
    body = {"email": EMAIL, "password": PASSWORD}
    if http.post("/api/auth/register", json=body).status_code != 201:
        request(http, "POST", "/api/auth/login", json=body)


def wait_until_ready(http: httpx.Client, notebook_id: str, timeout_s: int = 900) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sources = request(http, "GET", f"/api/notebooks/{notebook_id}/sources").json()
        line = ", ".join(f"{s['title']}: {s['status']} {s['progress']}%" for s in sources)
        print(f"\r  {line}".ljust(110), end="", flush=True)
        if all(s["status"] in ("ready", "failed") for s in sources):
            print()
            failed = [s for s in sources if s["status"] == "failed"]
            if failed:
                raise SystemExit(f"Ingestion failed: {[(s['title'], s['error']) for s in failed]}")
            return
        time.sleep(2)
    raise SystemExit("Timed out waiting for ingestion")


def evidence_rank(passages: list[dict[str, Any]], evidence: list[str]) -> int | None:
    for passage in passages:
        text = normalize(passage["text"])
        if all(normalize(e) in text for e in evidence):
            return int(passage["number"])
    return None


def iter_sse(lines: Iterator[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    event, data = "", ""
    for line in lines:
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data += line.removeprefix("data: ")
        elif not line and event:
            yield event, json.loads(data)
            event, data = "", ""


def ask(http: httpx.Client, notebook_id: str, question: str) -> dict[str, Any]:
    while True:
        started = time.perf_counter()
        out: dict[str, Any] = {"first_token_s": None, "passages": [], "answer": "", "error": None}
        with http.stream(
            "POST", f"/api/notebooks/{notebook_id}/chat", json={"message": question}, timeout=600
        ) as resp:
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("retry-after", "30")))
                continue
            resp.raise_for_status()
            for event, data in iter_sse(resp.iter_lines()):
                if event == "sources":
                    out["passages"] = data["passages"]
                elif event == "token" and out["first_token_s"] is None:
                    out["first_token_s"] = round(time.perf_counter() - started, 2)
                elif event in ("done", "error"):
                    out["answer"] = data["message"]["content"]
                    out["error"] = data.get("detail")
                    out["meta"] = data["message"]["meta"]
        out["total_s"] = round(time.perf_counter() - started, 2)
        return out


def score(case: Case, result: dict[str, Any]) -> None:
    answer = normalize(result["answer"])
    forbidden = any(normalize(f) in answer for f in case.forbid)
    if case.unanswerable:
        result["answer_ok"] = any(p in answer for p in NOT_FOUND_PATTERNS) and not forbidden
        return
    result["answer_ok"] = not forbidden and all(
        any(normalize(alt) in answer for alt in group) for group in case.expect
    )
    cited = {int(n) for n in CITATION.findall(result["answer"])}
    supporting = {
        p["number"]
        for p in result["passages"]
        if all(normalize(e) in normalize(p["text"]) for e in case.evidence)
    }
    result["cited"] = sorted(cited)
    result["citation_ok"] = bool(cited & supporting)


def run_case(
    http: httpx.Client, notebook_id: str, case: Case, retrieval_only: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": case.id,
        "unanswerable": case.unanswerable,
        "evidence": case.evidence,
    }
    if case.follow_up_of is None:
        request(http, "DELETE", f"/api/notebooks/{notebook_id}/messages")
        found = request(
            http, "POST", f"/api/notebooks/{notebook_id}/search", json={"query": case.question}
        ).json()
        result["top_similarity"] = found["top_similarity"]
        result["evidence_rank"] = evidence_rank(found["passages"], case.evidence)
    if retrieval_only:
        return result

    result.update(ask(http, notebook_id, case.question))
    if case.follow_up_of is not None:  # retrieval used the rewritten query; judge that
        result["top_similarity"] = result.get("meta", {}).get("top_similarity")
        result["evidence_rank"] = evidence_rank(result["passages"], case.evidence)
    score(case, result)
    return result


def yes(value: object) -> str:
    return "-" if value is None else ("yes" if value else "NO")


def report(results: list[dict[str, Any]], retrieval_only: bool) -> None:
    print(f"\n{'case':<18}{'evidence':>9}{'sim':>7}", end="")
    print("" if retrieval_only else f"{'answer':>8}{'cites':>7}{'1st tok':>9}{'total':>8}")
    for r in results:
        rank = (
            "-"
            if r["unanswerable"]
            else (f"#{r['evidence_rank']}" if r["evidence_rank"] else "MISS")
        )
        sim = f"{r['top_similarity']:.2f}" if r.get("top_similarity") is not None else "-"
        print(f"{r['id']:<18}{rank:>9}{sim:>7}", end="")
        if retrieval_only:
            print()
            continue
        first = f"{r['first_token_s']:.1f}s" if r.get("first_token_s") else "-"
        print(
            f"{yes(r['answer_ok']):>8}{yes(r.get('citation_ok')):>7}{first:>9}{r['total_s']:>7.1f}s"
        )

    answerable = [r for r in results if not r["unanswerable"]]
    refusals = [r for r in results if r["unanswerable"]]
    hits = [r for r in answerable if r["evidence_rank"]]
    mrr = sum(1 / r["evidence_rank"] for r in hits) / len(answerable)
    print(f"\nRetrieval: evidence found for {len(hits)}/{len(answerable)} (MRR {mrr:.2f})")
    if not retrieval_only:
        correct = sum(r["answer_ok"] for r in answerable)
        cites = sum(bool(r.get("citation_ok")) for r in answerable)
        refused = sum(r["answer_ok"] for r in refusals)
        firsts = [r["first_token_s"] for r in results if r.get("first_token_s")]
        print(
            f"Answers:   correct {correct}/{len(answerable)}, cited the supporting passage "
            f"{cites}/{len(answerable)}, declined unanswerable {refused}/{len(refusals)}"
        )
        print(
            f"Latency:   first token median {statistics.median(firsts):.1f}s, "
            f"answer median {statistics.median(r['total_s'] for r in results):.1f}s"
        )
    sims_ok = sorted(r["top_similarity"] for r in answerable if r.get("top_similarity") is not None)
    sims_no = sorted(r["top_similarity"] for r in refusals if r.get("top_similarity") is not None)
    if sims_ok and sims_no:
        print(
            f"Similarity: answerable min {sims_ok[0]:.2f} / "
            f"median {statistics.median(sims_ok):.2f}; unanswerable max {sims_no[-1]:.2f}  "
            "(RETRIEVAL_MIN_SIMILARITY should sit between)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the eval notebook afterwards")
    parser.add_argument("--cache", type=Path, default=Path("data/eval-cache"))
    parser.add_argument("--out", type=Path, default=Path("data/evals"))
    args = parser.parse_args()

    docs = documents(args.cache)
    with httpx.Client(base_url=args.base_url, timeout=60) as http:
        sign_in(http)
        title = f"Eval {datetime.now():%Y-%m-%d %H:%M}"
        notebook_id = request(http, "POST", "/api/notebooks", json={"title": title}).json()["id"]
        try:
            for name, data in docs.items():
                request(
                    http,
                    "POST",
                    f"/api/notebooks/{notebook_id}/sources",
                    files={"file": (name, data)},
                )
            print("Processing documents ...")
            wait_until_ready(http, notebook_id)
            results = []
            for case in CASES:
                print(f"  {case.id} ...", flush=True)
                results.append(run_case(http, notebook_id, case, args.retrieval_only))
        finally:
            if not args.keep:
                http.delete(f"/api/notebooks/{notebook_id}")

    report(results, args.retrieval_only)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results: {path}")


if __name__ == "__main__":
    main()
