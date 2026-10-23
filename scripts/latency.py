"""Measure /check latency of a running API on real RAGTruth test responses -> results/api_latency.json.

Each request is one whole test response (original text from data/raw/response.jsonl) against its source, as a
client would send it.
Requests go one at a time, so the numbers are single-stream latency on whatever CPU the container has.
Standard library only; start the API first (see README), then:
  python3.11 scripts/latency.py --url http://127.0.0.1:8000 --n 200
"""

import argparse
import json
import platform
import random
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hd.data import load_sources, load_split  # noqa: E402


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--n", type=int, default=200, help="test responses to send (fixed sample, seed 0)")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--note", default="", help="free text stored with the results, e.g. the CPU and container limits")
    args = ap.parse_args()

    responses = defaultdict(list)
    for r in load_split("test"):
        responses[r["response_id"]].append(r)
    sources = load_sources()
    with open(ROOT / "data" / "raw" / "response.jsonl") as f:
        text = {r["id"]: r["response"] for r in map(json.loads, f) if r["id"] in responses}
    ids = random.Random(0).sample(sorted(responses), args.n + args.warmup)

    rows = []
    for k, rid in enumerate(ids):
        first = responses[rid][0]
        payload = {"sources": [sources[first["source_id"]]], "answer": text[rid]}
        req = urllib.request.Request(args.url.rstrip("/") + "/check", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = json.load(resp)
        wall = 1000 * (time.perf_counter() - start)
        if k >= args.warmup:  # the first calls pay one-off costs (thread pools, allocator)
            rows.append({"sentences": len(body["sentences"]), "wall_ms": wall, "server_ms": body["latency_ms"],
                         "task": first["task_type"]})
        if (k + 1) % 25 == 0:
            print(f"{k + 1}/{len(ids)}", flush=True)

    per_req = [r["wall_ms"] for r in rows]
    total_sents = sum(r["sentences"] for r in rows)
    out = {
        "url": args.url, "requests": len(rows), "sentences": total_sents, "note": args.note,
        "client": {"platform": platform.platform(), "python": platform.python_version()},
        "ms_per_request": {"median": round(statistics.median(per_req), 1), "p95": round(percentile(per_req, 0.95), 1)},
        "ms_per_sentence": round(sum(per_req) / total_sents, 1),
        "sentences_per_second": round(1000 * total_sents / sum(per_req), 2),
        "by_task_ms_per_request": {t: round(statistics.median([r["wall_ms"] for r in rows if r["task"] == t]), 1)
                                   for t in sorted({r["task"] for r in rows})},
    }
    (ROOT / "results" / "api_latency.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
