"""LLM-as-judge baseline: Gemini reads the source and one sentence and says whether the sentence is supported.

Stdlib only (REST via urllib), so nothing is installed. Runs locally on a fixed random sample of test sentences,
one call per sentence with the same input the detector sees. Each answer is appended to results/gemini_test.jsonl
as it arrives, so an interrupted run (quota, network) resumes where it stopped.

Needs GEMINI_API_KEY in the environment or in .env at the repo root.
  PYTHONPATH=src python3 -m hd.judge --n 1000
"""

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

from hd.data import load_sources, load_split

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gemini-3.8-flash"
PRICE_PER_M = {"input": 0.75, "output": 3.75}  # USD, paid standard tier through 2026-12-31 (output includes thinking)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """You are checking whether a sentence written by an AI assistant is supported by its source material.

<source>
{source}
</source>

<sentence>
{sentence}
</sentence>

A sentence is "hallucinated" if any part of it is not supported by the source: it states a fact, number, name or
detail that the source does not contain, contradicts the source, or adds claims beyond it. Generic connective
phrasing with no factual claim counts as supported. Use only the source, not outside knowledge.
Answer with the verdict for the sentence."""

SCHEMA = {"type": "OBJECT", "properties": {"verdict": {"type": "STRING", "enum": ["supported", "hallucinated"]}},
          "required": ["verdict"]}


def api_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("Set GEMINI_API_KEY in the environment or in .env at the repo root")


def sample_test(n: int, seed: int = 0) -> list[dict]:
    rows = load_split("test")
    return sorted(random.Random(seed).sample(rows, n), key=lambda r: r["id"])


class RateLimiter:
    """Keep at most `rpm` requests in any 60-second window."""

    def __init__(self, rpm: int):
        self.rpm, self.sent = rpm, []

    def wait(self) -> None:
        now = time.monotonic()
        self.sent = [t for t in self.sent if now - t < 60]
        if len(self.sent) >= self.rpm:
            time.sleep(60 - (now - self.sent[0]) + 0.05)
        self.sent.append(time.monotonic())


def parse(resp: dict) -> tuple[int, dict]:
    """Return (1 if hallucinated else 0, token usage) from a generateContent response."""
    text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"] if not p.get("thought"))
    verdict = json.loads(text)["verdict"]
    if verdict not in ("supported", "hallucinated"):
        raise ValueError(f"unexpected verdict {verdict!r}")
    u = resp.get("usageMetadata", {})
    usage = {"input": u.get("promptTokenCount", 0),
             "output": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)}
    return int(verdict == "hallucinated"), usage


class Judge:
    def __init__(self, model: str, key: str, rpm: int, retries: int = 5):
        self.url, self.key = URL.format(model=model), key
        self.limiter, self.retries = RateLimiter(rpm), retries

    def post(self, body: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    def __call__(self, source: str, sentence: str) -> tuple[int, dict, float]:
        """(label, usage, seconds for the successful call). Retries 429/5xx and malformed answers with backoff."""
        body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(source=source, sentence=sentence)}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "responseSchema": SCHEMA}}
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            start = time.perf_counter()
            try:
                resp = self.post(body)
                label, usage = parse(resp)
                return label, usage, time.perf_counter() - start
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise SystemExit(f"Gemini HTTP {e.code}: {detail}") from e
                if e.code == 429 and "PerDay" in detail:
                    raise SystemExit("Daily quota used up; rerun tomorrow and it resumes where it stopped") from e
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
                if attempt == self.retries:
                    raise SystemExit(f"Gemini call failed after retries: {e!r}") from e
            time.sleep(min(60, 2 ** attempt * 2))
        raise AssertionError("unreachable")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000, help="test sentences to judge (fixed sample, seed 0)")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--rpm", type=int, default=10, help="requests per minute (free tier limits are low)")
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    path = args.out / "gemini_test.jsonl"
    done = {json.loads(line)["id"] for line in open(path)} if path.exists() else set()
    rows = [r for r in sample_test(args.n) if r["id"] not in done]
    print(f"{len(done)} already judged, {len(rows)} to go with {args.model}", flush=True)
    sources = load_sources()
    judge = Judge(args.model, api_key(), args.rpm)
    with open(path, "a") as f:
        for i, r in enumerate(rows, 1):
            label, usage, secs = judge(sources[r["source_id"]], r["sentence"])
            f.write(json.dumps({"id": r["id"], "task_type": r["task_type"], "model": r["model"], "label": r["label"],
                                "score": float(label), "llm": args.model, "seconds": round(secs, 3), **usage}) + "\n")
            f.flush()
            if i % 25 == 0 or i == len(rows):
                print(f"{len(done) + i}/{args.n}", flush=True)


if __name__ == "__main__":
    main()
