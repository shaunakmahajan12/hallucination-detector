"""Metrics from saved predictions, so every reported number can be reproduced from files in results/.

The threshold is picked on validation F1 and then applied unchanged to test.
Usage: python eval/evaluate.py --method nli   (reads results/nli_{val,test}.jsonl, writes results/nli_metrics.json)
       python eval/evaluate.py --judge gemini (LLM judge on its test sample, next to every other method on the same
                                               sentences at that method's val threshold -> results/gemini_metrics.json)
       python eval/evaluate.py --aggrefact    (out-of-domain balanced accuracy per dataset -> aggrefact_metrics.json)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hd.data import read_jsonl  # noqa: E402
from hd.judge import PRICE_PER_M  # noqa: E402
from hd.metrics import (auroc, balanced_accuracy, best_bacc_threshold, best_threshold, calibration,  # noqa: E402
                        platt_apply, platt_fit, report)


def grouped(rows: list[dict], key: str, threshold: float) -> dict:
    out = {}
    for value in sorted({r[key] for r in rows}):
        sub = [r for r in rows if r[key] == value]
        out[value] = report([r["label"] for r in sub], [r["score"] for r in sub], threshold)
    return out


def judge_report(name: str, results: Path) -> dict:
    """The judge's verdicts are 0/1, so its threshold is 0.5 and its AUROC is balanced accuracy."""
    rows = read_jsonl(results / f"{name}_test.jsonl")
    ids = {r["id"] for r in rows}
    secs = sorted(r["seconds"] for r in rows)
    tokens = {k: sum(r[k] for r in rows) / len(rows) for k in ("input", "output")}
    usd = sum(tokens[k] * PRICE_PER_M[k] for k in tokens) / 1e6
    out = {"judge": rows[0]["llm"], "sample": len(rows),
           "methods": {name: report([r["label"] for r in rows], [r["score"] for r in rows], 0.5)},
           "by_task": grouped(rows, "task_type", 0.5),
           "latency_s": {"median": secs[len(secs) // 2], "p95": secs[int(0.95 * (len(secs) - 1))]},
           "tokens_per_check": {k: round(v) for k, v in tokens.items()},
           "usd_per_1000_checks_paid_tier": round(1000 * usd, 3)}
    for other in sorted(results.glob("*_metrics.json")):
        method = other.name.removesuffix("_metrics.json")
        if method == name or not (results / f"{method}_test.jsonl").exists():
            continue
        t = json.loads(other.read_text()).get("threshold_from_val")
        if t is None:  # aggrefact_metrics.json is a benchmark summary, not a method on this sample
            continue
        sub = [r for r in read_jsonl(results / f"{method}_test.jsonl") if r["id"] in ids]
        out["methods"][method] = report([r["label"] for r in sub], [r["score"] for r in sub], t)
    return out


def aggrefact_report(results: Path) -> dict:
    """Balanced accuracy (the benchmark's metric) per dataset under two thresholds: the RAGTruth val threshold unchanged
    (strictly out-of-domain, nothing tuned on AggreFact) and a per-dataset threshold tuned on AggreFact dev."""
    fixed = json.loads((results / "ft_metrics.json").read_text())["threshold_from_val"]
    dev, test = read_jsonl(results / "aggrefact_dev.jsonl"), read_jsonl(results / "aggrefact_test.jsonl")
    per = {}
    for name in sorted({r["dataset"] for r in test}):
        d = [r for r in dev if r["dataset"] == name]
        te = [r for r in test if r["dataset"] == name]
        labels, scores = [r["label"] for r in te], [r["score"] for r in te]
        t = best_bacc_threshold([r["label"] for r in d], [r["score"] for r in d]) if d else fixed
        per[name] = {"n": len(te), "unsupported": sum(labels), "auroc": round(auroc(labels, scores), 4),
                     "bacc_ragtruth_threshold": round(balanced_accuracy(labels, [int(s >= fixed) for s in scores]), 4),
                     "bacc_dev_threshold": round(balanced_accuracy(labels, [int(s >= t) for s in scores]), 4),
                     "dev_threshold": round(t, 6), "multi_window_frac": round(sum(r["windows"] > 1 for r in te) / len(te), 4)}

    def mean(key: str, names: list[str]) -> float:
        return round(sum(per[n][key] for n in names) / len(names), 4)

    ood = [n for n in per if "ragtruth" not in n.lower()]  # RAGTruth is in-domain for us
    keys = ("auroc", "bacc_ragtruth_threshold", "bacc_dev_threshold")
    return {"ragtruth_threshold": fixed,
            "average_out_of_domain": {"datasets": len(ood), **{k: mean(k, ood) for k in keys}},
            "average_all": {"datasets": len(per), **{k: mean(k, list(per)) for k in keys}},
            "per_dataset": per}


def main() -> None:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--method")
    group.add_argument("--judge")
    group.add_argument("--aggrefact", action="store_true")
    ap.add_argument("--results", type=Path, default=Path("results"))
    args = ap.parse_args()
    if args.judge:
        metrics = judge_report(args.judge, args.results)
        (args.results / f"{args.judge}_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        print(json.dumps(metrics, indent=2))
        return
    if args.aggrefact:
        metrics = aggrefact_report(args.results)
        (args.results / "aggrefact_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        print(json.dumps(metrics, indent=2))
        return

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
    # Calibration on test: raw scores, then Platt scaling fitted on val (monotonic, so F1/AUROC above are unchanged)
    a, b = platt_fit([r["label"] for r in val], [r["score"] for r in val])
    test_labels, test_scores = [r["label"] for r in test], [r["score"] for r in test]
    metrics["calibration_test"] = {"raw": calibration(test_labels, test_scores),
                                   "platt_from_val": {"a": round(a, 4), "b": round(b, 4),
                                                      **calibration(test_labels, platt_apply(test_scores, a, b))}}
    (args.results / f"{args.method}_metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
