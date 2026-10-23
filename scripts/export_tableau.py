"""Flatten the saved predictions in results/ into tidy CSVs for a Tableau Public dashboard -> tableau/data/.

  ragtruth_predictions.csv   one row per (test sentence, method): fine-tuned ModernBERT, NLI baseline, Gemini judge
  headline.csv               precision, recall, F1, AUROC per method, on the full test set and on the Gemini sample
  threshold_curve.csv        fine-tuned precision, recall and F1 at every threshold from 0.00 to 1.00 (step 0.01)
  aggrefact_predictions.csv  one row per LLM-AggreFact test claim, with the RAGTruth and per-dataset dev thresholds
  aggrefact_summary.csv      per-dataset AUROC and balanced accuracy from results/aggrefact_metrics.json
  calibration_bins.csv       reliability bins, raw scores and Platt-calibrated, from results/ft_metrics.json
  error_rates.csv            recall and false-alarm rate by slice, from results/ft_error_breakdown.json
  speed.csv                  measured time per sentence for each method and the hardware it ran on

Label 1 means hallucinated (unsupported) everywhere, and a higher score means more likely hallucinated. Before
writing, the CSVs are checked against the saved metrics (F1, AUROC, ECE, balanced accuracy), so the dashboard
can only show numbers the repo already reports. Standard library only.
"""

import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from hd.metrics import auroc, balanced_accuracy, calibration, platt_apply, prf, report  # noqa: E402

RESULTS = ROOT / "results"
OUT = ROOT / "tableau" / "data"
METHODS = {"ft": "Fine-tuned ModernBERT", "nli": "NLI baseline (DeBERTa-v3)", "gemini": "Gemini 3.5 Flash-Lite judge"}


def jsonl(name: str) -> list[dict]:
    with open(RESULTS / name) as f:
        return [json.loads(line) for line in f]


def load(name: str) -> dict:
    return json.loads((RESULTS / name).read_text())


def write(name: str, rows: list[dict]) -> None:
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote tableau/data/{name}: {len(rows):,} rows")


def check(what: str, got: float, want: float, tol: float = 5e-4) -> None:
    if abs(got - want) > tol:
        sys.exit(f"MISMATCH {what}: CSV gives {got:.4f}, results/ says {want:.4f}")
    print(f"  ok {what}: {got:.4f}")


def ragtruth() -> list[dict]:
    ft_m, nli_m = load("ft_metrics.json"), load("nli_metrics.json")
    platt = ft_m["calibration_test"]["platt_from_val"]
    ft, nli, gem = jsonl("ft_test.jsonl"), jsonl("nli_test.jsonl"), jsonl("gemini_test.jsonl")
    sample = {r["id"] for r in gem}
    ft_cal = platt_apply([r["score"] for r in ft], platt["a"], platt["b"])

    check("fine-tuned test F1", report([r["label"] for r in ft], [r["score"] for r in ft],
                                       ft_m["threshold_from_val"])["f1"], ft_m["test"]["f1"])
    check("fine-tuned test AUROC", auroc([r["label"] for r in ft], [r["score"] for r in ft]), ft_m["test"]["auroc"])
    check("NLI test F1", report([r["label"] for r in nli], [r["score"] for r in nli],
                                nli_m["threshold_from_val"])["f1"], nli_m["test"]["f1"])
    check("calibration ECE raw", calibration([r["label"] for r in ft], [r["score"] for r in ft])["ece"],
          ft_m["calibration_test"]["raw"]["ece"])
    check("calibration ECE Platt", calibration([r["label"] for r in ft], ft_cal)["ece"], platt["ece"])
    labels = {r["id"]: r["label"] for r in ft}
    if any(labels[r["id"]] != r["label"] for r in gem):
        sys.exit("MISMATCH: a Gemini row's label differs from the RAGTruth test label")

    rows = []
    for key, preds, thr, cal in (("ft", ft, ft_m["threshold_from_val"], ft_cal),
                                 ("nli", nli, nli_m["threshold_from_val"], None),
                                 ("gemini", gem, 0.5, None)):  # the judge answers 0 or 1, so any cut in (0, 1) works
        for i, r in enumerate(preds):
            rows.append({"sentence_id": r["id"], "method": METHODS[key], "task_type": r["task_type"],
                         "generator_llm": r["model"], "label": r["label"], "score": r["score"],
                         "score_calibrated": round(cal[i], 6) if cal else "", "val_threshold": thr,
                         "predicted": int(r["score"] >= thr), "in_gemini_sample": int(r["id"] in sample)})
    return rows


def headline(rt: list[dict]) -> list[dict]:
    """Each method at its validation threshold. The Gemini judge only covers a sample, so all three are also scored
    on exactly that sample for a like-for-like comparison."""
    rows = []
    for scope, keep in (("Full RAGTruth test", lambda r: True), ("Gemini sample", lambda r: r["in_gemini_sample"])):
        for method in METHODS.values():
            sub = [r for r in rt if r["method"] == method and keep(r)]
            if scope == "Full RAGTruth test" and method == METHODS["gemini"]:
                continue  # the judge never saw the full set
            m = report([r["label"] for r in sub], [r["score"] for r in sub], sub[0]["val_threshold"])
            rows.append({"scope": scope, "method": method, "sentences": m["n"], "hallucinated": m["hallucinated"],
                         "precision": m["precision"], "recall": m["recall"], "f1": m["f1"], "auroc": m["auroc"]})
    return rows


def threshold_curve(rt: list[dict]) -> list[dict]:
    ft = [r for r in rt if r["method"] == METHODS["ft"]]
    labels = [r["label"] for r in ft]
    rows = []
    for i in range(101):
        t = i / 100
        m = prf(labels, [int(r["score"] >= t) for r in ft])
        flagged = sum(r["score"] >= t for r in ft)
        row = {"threshold": t, **{k: round(v, 4) for k, v in m.items()}, "flagged": flagged}
        if not flagged:
            row["precision"] = ""  # undefined with nothing flagged; blank so the line ends instead of dropping to 0
        rows.append(row)
    return rows


def aggrefact() -> tuple[list[dict], list[dict]]:
    m = load("aggrefact_metrics.json")
    per = m["per_dataset"]
    preds = jsonl("aggrefact_test.jsonl")
    rows = [{"claim_id": r["id"], "dataset": r["dataset"],
             "domain": "In-domain (RAGTruth)" if r["dataset"] == "RAGTruth" else "Out-of-domain",
             "label": r["label"], "score": r["score"], "windows": r["windows"],
             "ragtruth_threshold": m["ragtruth_threshold"], "dev_threshold": per[r["dataset"]]["dev_threshold"]}
            for r in preds]
    baccs = []
    for name, d in per.items():
        sub = [r for r in rows if r["dataset"] == name]
        b = balanced_accuracy([r["label"] for r in sub], [int(r["score"] >= d["dev_threshold"]) for r in sub])
        if abs(b - d["bacc_dev_threshold"]) > 5e-4:
            sys.exit(f"MISMATCH {name} balanced accuracy: {b:.4f} vs {d['bacc_dev_threshold']}")
        baccs.append(b)
    check("LLM-AggreFact mean balanced accuracy, 11 datasets", statistics.mean(baccs),
          m["average_all"]["bacc_dev_threshold"])
    summary = [{"dataset": name, "domain": "In-domain (RAGTruth)" if name == "RAGTruth" else "Out-of-domain",
                "claims": d["n"], "unsupported": d["unsupported"], "auroc": d["auroc"],
                "bacc_dev_threshold": d["bacc_dev_threshold"], "bacc_ragtruth_threshold": d["bacc_ragtruth_threshold"],
                "dev_threshold": d["dev_threshold"]} for name, d in per.items()]
    return rows, summary


def calibration_bins() -> list[dict]:
    cal = load("ft_metrics.json")["calibration_test"]
    rows = [{"version": version, "ece": c["ece"], "bin": b["bin"],
             "bin_mid": round(float(b["bin"].split("-")[0]) + 0.05, 2), "sentences": b["n"],
             "mean_score": b["mean_score"], "observed_rate": b["hallucinated_rate"]}
            for version, c in (("Raw score", cal["raw"]), ("Platt-calibrated", cal["platt_from_val"]))
            for b in c["bins"]]
    # the diagonal as its own series, so the chart needs no dual axis
    rows += [{"version": "Perfect calibration", "ece": "", "bin": "", "bin_mid": v, "sentences": 0, "mean_score": v,
              "observed_rate": v} for v in (0.0, 1.0)]
    return rows


def error_rates() -> list[dict]:
    e = load("ft_error_breakdown.json")
    rows = []
    for key, slices in e.items():
        if not isinstance(slices, dict):
            continue
        rate = "Recall (hallucinations flagged)" if key.startswith("recall_by_") else "False-alarm rate"
        dim = key.removeprefix("recall_by_").removeprefix("false_alarm_rate_by_")
        rows += [{"rate": rate, "dimension": dim, "slice": s, "sentences": v["n"], "value": v["flagged"]}
                 for s, v in slices.items()]
    return rows


def speed() -> list[dict]:
    gem = jsonl("gemini_test.jsonl")
    api, agg, nli = load("api_latency.json"), load("aggrefact_timing.json"), load("nli_timing.json")
    rows = [
        {"method": METHODS["ft"], "setting": "Docker API, 4 CPU cores (Apple M2)", "ms_per_sentence": api["ms_per_sentence"],
         "sentences_measured": api["sentences"], "note": "one request at a time, fp32",
         "label": "Fine-tuned, Docker API on 4 CPU cores"},
        {"method": METHODS["ft"], "setting": "Batch scoring, Tesla T4 GPU", "ms_per_sentence": agg["test"]["ms_per_claim"],
         "sentences_measured": agg["test"]["rows"], "note": "LLM-AggreFact test claims",
         "label": "Fine-tuned, Tesla T4 GPU (batch)"},
        {"method": METHODS["nli"], "setting": "Batch scoring, Tesla T4 GPU", "ms_per_sentence": nli["test"]["ms_per_sentence"],
         "sentences_measured": nli["test"]["sentences"], "note": "RAGTruth test", "label": "NLI baseline, Tesla T4 GPU (batch)"},
        {"method": METHODS["gemini"], "setting": "API call, free tier", "ms_per_sentence":
         round(1000 * statistics.median(r["seconds"] for r in gem), 1), "sentences_measured": len(gem),
         "note": "median per call, includes rate limiting, 987 of a planned 1,000 (free-tier quota)",
         "label": "Gemini Flash-Lite judge, API (median)"},
    ]
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("checking CSVs against results/")
    rt = ragtruth()
    agg, agg_summary = aggrefact()
    write("ragtruth_predictions.csv", rt)
    write("headline.csv", headline(rt))
    write("threshold_curve.csv", threshold_curve(rt))
    write("aggrefact_predictions.csv", agg)
    write("aggrefact_summary.csv", agg_summary)
    write("calibration_bins.csv", calibration_bins())
    write("error_rates.csv", error_rates())
    write("speed.csv", speed())


if __name__ == "__main__":
    main()
