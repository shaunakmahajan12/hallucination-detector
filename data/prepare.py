"""RAGTruth -> sentence-level pairs.

Reads data/raw/{response,source_info}.jsonl (from github.com/ParticleMedia/RAGTruth) and writes data/processed/:
  sources.jsonl              one row per source: source_id, task_type, text (stored once, not per sentence)
  {train,val,test}.jsonl     one row per response sentence: source_id, sentence, label (1 = hallucinated), ...
  stats.json                 class balance and source lengths per split

Validation is carved out of train by source_id, because every source has responses from 6 LLMs and a random
split would leak. Standard library only, so it runs anywhere (laptop or Kaggle) in a few seconds.
"""

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# A sentence ends at . ! or ? (optionally followed by a closing quote or bracket) and whitespace, or at a newline.
SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+|\n+")
# Initials, common abbreviations and list numbers shouldn't end a sentence ("Dr. Smith", "J. K. Rowling", "1. Open").
NO_SPLIT_BEFORE = re.compile(r"(\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|e\.g|i\.e|No|Inc|Ltd|Co|U\.S)|^\s*\d{1,2})\.$")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character offsets of each sentence, so annotated spans can be mapped onto them."""
    spans, start = [], 0
    for m in SENTENCE_END.finditer(text):
        if m.group().strip() == "" and NO_SPLIT_BEFORE.search(text[start : m.start()]):
            continue
        spans.append((start, m.start() + len(m.group().rstrip())))
        start = m.end()
    spans.append((start, len(text)))
    # Trim surrounding whitespace and drop empty pieces.
    out = []
    for s, e in spans:
        piece = text[s:e]
        if piece.strip():
            lead = len(piece) - len(piece.lstrip())
            out.append((s + lead, s + len(piece.rstrip())))
    return out


def source_text(src: dict) -> str:
    info = src["source_info"]
    if src["task_type"] == "QA":
        return f"Question: {info['question']}\n\n{info['passages']}"
    if src["task_type"] == "Data2txt":
        return json.dumps(info, ensure_ascii=False)
    return info  # Summary: the article itself


def split_of(row: dict, val_frac: float) -> str:
    if row["split"] == "test":
        return "test"
    bucket = int(hashlib.sha256(row["source_id"].encode()).hexdigest(), 16) % 1000
    return "val" if bucket < val_frac * 1000 else "train"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    ap.add_argument("--out", type=Path, default=ROOT / "processed")
    ap.add_argument("--val-frac", type=float, default=0.1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sources = {}
    with open(args.raw / "source_info.jsonl") as f:
        for line in f:
            s = json.loads(line)
            sources[s["source_id"]] = {"source_id": s["source_id"], "task_type": s["task_type"], "text": source_text(s)}
    with open(args.out / "sources.jsonl", "w") as f:
        for s in sources.values():
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    rows = {"train": [], "val": [], "test": []}
    skipped = Counter()
    with open(args.raw / "response.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if r["quality"] != "good":  # incorrect refusals and truncated responses
                skipped[r["quality"]] += 1
                continue
            split = split_of(r, args.val_frac)
            for i, (s, e) in enumerate(split_sentences(r["response"])):
                overlapping = [lab for lab in r["labels"] if lab["start"] < e and lab["end"] > s]
                rows[split].append({
                    "id": f"{r['id']}-{i}",
                    "response_id": r["id"],
                    "source_id": r["source_id"],
                    "task_type": sources[r["source_id"]]["task_type"],
                    "model": r["model"],
                    "sentence_idx": i,
                    "sentence": r["response"][s:e],
                    "label": int(bool(overlapping)),
                    "label_types": sorted({lab["label_type"] for lab in overlapping}),
                })

    ids = {k: {row["source_id"] for row in v} for k, v in rows.items()}
    assert not ids["train"] & ids["val"] and not ids["train"] & ids["test"] and not ids["val"] & ids["test"], \
        "source_id leaks across splits"

    stats = {"skipped_responses": dict(skipped)}
    for split, items in rows.items():
        with open(args.out / f"{split}.jsonl", "w") as f:
            for row in items:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        src_words = [len(sources[sid]["text"].split()) for sid in ids[split]]
        pos = sum(row["label"] for row in items)
        stats[split] = {
            "sentences": len(items),
            "responses": len({row["response_id"] for row in items}),
            "sources": len(ids[split]),
            "hallucinated": pos,
            "hallucinated_frac": round(pos / len(items), 4),
            "hallucinated_frac_by_task": {
                t: round(sum(x["label"] for x in items if x["task_type"] == t)
                         / max(1, sum(x["task_type"] == t for x in items)), 4)
                for t in ("QA", "Summary", "Data2txt")
            },
            "source_words": {"median": statistics.median(src_words), "p95": sorted(src_words)[int(0.95 * len(src_words))],
                             "max": max(src_words)},
            "sentence_words_median": statistics.median(len(x["sentence"].split()) for x in items),
        }
    (args.out / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats, indent=2))
    print("No source_id appears in more than one split.")


if __name__ == "__main__":
    main()
