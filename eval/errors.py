"""Error analysis for one method's test predictions: where recall and precision break down, plus a fixed sample of
misclassified sentences (with the annotators' spans and notes) to read and sort into failure types.

Needs data/raw/response.jsonl for the annotated spans.
Usage: python eval/errors.py --method ft   (writes results/ft_error_breakdown.json and results/ft_errors_sample.md)
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "data"))
from hd.data import load_sources, load_split, read_jsonl  # noqa: E402
from prepare import split_sentences  # noqa: E402


def spans_by_sentence(raw: Path, ids: set[str]) -> dict[str, list[dict]]:
    """Annotated spans overlapping each sentence, with how many of their characters fall inside it."""
    out = {}
    with open(raw) as f:
        for line in f:
            r = json.loads(line)
            if r["quality"] != "good":  # prepare.py drops these, so their sentence ids never match
                continue
            for i, (s, e) in enumerate(split_sentences(r["response"])):
                sid = f"{r['id']}-{i}"
                if sid not in ids:
                    continue
                out[sid] = [{"text": lab["text"], "type": lab["label_type"], "note": lab["meta"],
                             "implicit_true": lab["implicit_true"], "due_to_null": lab["due_to_null"],
                             "chars_inside": min(e, lab["end"]) - max(s, lab["start"])}
                            for lab in r["labels"] if lab["start"] < e and lab["end"] > s]
    return out


def rate(rows: list[dict], key) -> dict:
    """Recall for positives (or flag rate for negatives) per group."""
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r["pred"])
    return {g: {"n": len(v), "flagged": round(sum(v) / len(v), 3)} for g, v in sorted(groups.items())}


def bucket(n: int, edges: list[int]) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= n < hi:
            return f"{lo}-{hi - 1}"
    return f"{edges[-1]}+"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True)
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / "response.jsonl")
    ap.add_argument("--fn", type=int, default=30, help="missed hallucinations to sample")
    ap.add_argument("--fp", type=int, default=20, help="false alarms to sample")
    args = ap.parse_args()

    t = json.loads((args.results / f"{args.method}_metrics.json").read_text())["threshold_from_val"]
    score = {r["id"]: r["score"] for r in read_jsonl(args.results / f"{args.method}_test.jsonl")}
    rows = load_split("test")
    for r in rows:
        r["score"], r["pred"] = score[r["id"]], int(score[r["id"]] >= t)
    spans = spans_by_sentence(args.raw, {r["id"] for r in rows if r["label"]})
    pos = [r for r in rows if r["label"]]
    neg = [r for r in rows if not r["label"]]
    for r in pos:
        r["spans"] = spans[r["id"]]
        r["max_inside"] = max(s["chars_inside"] for s in r["spans"])

    breakdown = {
        "method": args.method, "threshold": t,
        "recall_by_task": rate(pos, lambda r: r["task_type"]),
        "recall_by_label_type": rate([dict(r, lt=lt) for r in pos for lt in r["label_types"]], lambda r: r["lt"]),
        # how much of the annotated span is actually in this sentence: tiny overlaps are spans spilling over a boundary
        "recall_by_span_chars_inside": rate(pos, lambda r: bucket(r["max_inside"], [1, 3, 10, 30, 100])),
        "recall_by_implicit_true": rate(pos, lambda r: any(s["implicit_true"] for s in r["spans"])),
        "recall_by_due_to_null": rate(pos, lambda r: any(s["due_to_null"] for s in r["spans"])),
        "recall_by_sentence_words": rate(pos, lambda r: bucket(len(r["sentence"].split()), [0, 8, 16, 32, 64])),
        "recall_by_position": rate(pos, lambda r: bucket(r["sentence_idx"], [0, 1, 3, 6, 10])),
        "false_alarm_rate_by_task": rate(neg, lambda r: r["task_type"]),
        "false_alarm_rate_by_sentence_words": rate(neg, lambda r: bucket(len(r["sentence"].split()), [0, 8, 16, 32, 64])),
    }
    (args.results / f"{args.method}_error_breakdown.json").write_text(json.dumps(breakdown, indent=2) + "\n")
    print(json.dumps(breakdown, indent=2))

    # Fixed sample, spread across tasks, for reading by hand
    rng = random.Random(0)
    sample = []
    for kind, pool, n in (("missed", [r for r in pos if not r["pred"]], args.fn),
                          ("false alarm", [r for r in neg if r["pred"]], args.fp)):
        by_task = defaultdict(list)
        for r in pool:
            by_task[r["task_type"]].append(r)
        tasks = sorted(by_task)
        for i, task in enumerate(tasks):
            k = n // len(tasks) + (i < n % len(tasks))
            sample += [(kind, r) for r in rng.sample(by_task[task], min(k, len(by_task[task])))]
    sources = load_sources()
    sentences = {(r["response_id"], r["sentence_idx"]): r["sentence"] for r in rows}
    lines = [f"# {args.method} errors on test: {args.fn} missed hallucinations, {args.fp} false alarms\n",
             f"Threshold {t}. Sample is fixed (seed 0) and spread evenly across tasks.\n"]
    for n, (kind, r) in enumerate(sample, 1):
        prev = sentences.get((r["response_id"], r["sentence_idx"] - 1), "")
        lines += [f"## {n}. {kind} | {r['task_type']} | {r['model']} | id {r['id']} | score {r['score']:.3f}\n",
                  f"**Previous sentence:** {prev or '(none)'}\n", f"**Sentence:** {r['sentence']}\n"]
        for s in r.get("spans", []):
            lines.append(f"- span ({s['type']}, {s['chars_inside']} chars inside, implicit_true={s['implicit_true']}, "
                         f"due_to_null={s['due_to_null']}): \"{s['text']}\"  \n  note: {' '.join(s['note'].split())}")
        lines += ["", "<details><summary>source</summary>\n", sources[r["source_id"]], "\n</details>\n"]
    (args.results / f"{args.method}_errors_sample.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
