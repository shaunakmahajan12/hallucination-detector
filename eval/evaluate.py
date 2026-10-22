"""Metrics from saved predictions, so every reported number can be reproduced from files in results/.

The threshold is picked on validation F1 and then applied unchanged to test.
Usage: python eval/evaluate.py --method nli   (reads results/nli_{val,test}.jsonl, writes results/nli_metrics.json)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hd.data import read_jsonl  # noqa: E402
from hd.metrics import best_threshold, report  # noqa: E402


def grouped(rows: list[dict], key: str, threshold: float) -> dict:
    out = {}
    for value in sorted({r[key] for r in rows}):
        sub = [r for r in rows if r[key] == value]
        out[value] = report([r["label"] for r in sub], [r["score"] for r in sub], threshold)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True)
    ap.add_argument("--results", type=Path, default=Path("results"))
    args = ap.parse_args()

    val = read_jsonl(args.results / f"{args.method}_val.jsonl")
    test = read_jsonl(args.results / f"{args.method}_test.jsonl")
    t = best_threshold([r["label"] for r in val], [r["score"] for r in val])
    metrics = {
        "method": args.method,
        "threshold_from_val": round(t, 6),
        "val": report([r["label"] for r in val], [r["score"] for r in val], t),
        "test": report([r["label"] for r in test], [r["score"] for r in test], t),
        "test_by_task": grouped(test, "task_type", t),
        "test_by_llm": grouped(test, "model", t),
    }
    (args.results / f"{args.method}_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
