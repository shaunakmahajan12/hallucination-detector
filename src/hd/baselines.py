"""Zero-shot NLI baseline: a sentence is hallucinated when no chunk of its source entails it.

score = 1 - max over source chunks of P(entailment | chunk, sentence), so higher means more likely hallucinated.
No training. Run on a GPU (Kaggle): PYTHONPATH=src python -m hd.baselines --splits val test
"""

import argparse
import json
import time
from pathlib import Path

from hd.data import chunk_words, load_sources, load_split

NLI_MODEL = "cross-encoder/nli-deberta-v3-base"


class NLIScorer:
    def __init__(self, model_name: str = NLI_MODEL, batch_size: int = 64):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name).to(self.device).eval()
        if self.device == "cuda":
            self.model.half()
        labels = {name.lower(): i for i, name in self.model.config.id2label.items()}
        self.entail_idx = labels["entailment"]
        self.batch_size = batch_size

    def __call__(self, pairs: list[tuple[str, str]]) -> list[float]:
        """P(entailment) for each (premise, hypothesis) pair. Pairs are batched by length to cut padding."""
        order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][0]))
        probs = [0.0] * len(pairs)
        with self.torch.inference_mode():
            for b in range(0, len(order), self.batch_size):
                idx = order[b : b + self.batch_size]
                enc = self.tok([pairs[i][0] for i in idx], [pairs[i][1] for i in idx], truncation="only_first",
                               max_length=512, padding=True, return_tensors="pt").to(self.device)
                p = self.model(**enc).logits.float().softmax(-1)[:, self.entail_idx].tolist()
                for i, v in zip(idx, p):
                    probs[i] = v
        return probs


def score_rows(rows: list[dict], sources: dict[str, str], entailment, chunk_size: int = 250) -> tuple[list[float], int]:
    """Return (hallucination score per row, number of NLI pairs run)."""
    chunks = {sid: chunk_words(sources[sid], chunk_size) for sid in {r["source_id"] for r in rows}}
    pairs, owner = [], []
    for i, r in enumerate(rows):
        for c in chunks[r["source_id"]]:
            pairs.append((c, r["sentence"]))
            owner.append(i)
    best = [0.0] * len(rows)
    for i, p in zip(owner, entailment(pairs)):
        best[i] = max(best[i], p)
    return [1.0 - b for b in best], len(pairs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["val", "test"])
    ap.add_argument("--limit", type=int, help="score only the first N sentences per split (smoke run)")
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sources = load_sources()
    scorer = NLIScorer()
    gpu = scorer.torch.cuda.get_device_name(0) if scorer.device == "cuda" else "cpu"
    timing = {"model": NLI_MODEL, "device": gpu}
    for split in args.splits:
        rows = load_split(split, limit=args.limit)
        start = time.perf_counter()
        scores, n_pairs = score_rows(rows, sources, scorer)
        seconds = time.perf_counter() - start
        with open(args.out / f"nli_{split}.jsonl", "w") as f:
            for r, s in zip(rows, scores):
                f.write(json.dumps({"id": r["id"], "task_type": r["task_type"], "model": r["model"],
                                    "label": r["label"], "score": round(s, 6)}) + "\n")
        timing[split] = {"sentences": len(rows), "nli_pairs": n_pairs, "seconds": round(seconds, 1),
                         "ms_per_sentence": round(1000 * seconds / len(rows), 2)}
        print(split, timing[split], flush=True)
    (args.out / "nli_timing.json").write_text(json.dumps(timing, indent=2) + "\n")


if __name__ == "__main__":
    main()
