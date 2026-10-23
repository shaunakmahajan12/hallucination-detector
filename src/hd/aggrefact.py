"""Out-of-domain test: score LLM-AggreFact (11 grounding datasets, human labels) with the fine-tuned detector.

The dataset is gated (accept its terms on the Hub once) and licensed for evaluation only, never training.
AggreFact's label is 1 = supported; ours is 1 = hallucinated, so rows are written with label = 1 - label.
Documents longer than the window are split into overlapping token windows; a claim is supported if any window
supports it, so its score is the minimum over windows.

Run on a GPU (Kaggle), with HF_TOKEN set:  PYTHONPATH=src python -m hd.aggrefact --model <hub repo>
"""

import argparse
import json
import os
import time
from pathlib import Path

from hd.model import collate, load_model, make_batches, pack

DATASET = "lytang/LLM-AggreFact"


def windows(doc_ids: list[int], room: int, overlap: int) -> list[list[int]]:
    if len(doc_ids) <= room:
        return [doc_ids]
    step = room - overlap
    return [doc_ids[i : i + room] for i in range(0, len(doc_ids) - overlap, step)]


def build(claim_ids: list[list[int]], doc_ids: list[list[int]], max_len: int, overlap: int, cls_id: int,
          sep_id: int) -> tuple[list[list[int]], list[int]]:
    """Packed sequences for every (claim, document window) pair, and the row each sequence belongs to."""
    seqs, owner = [], []
    for row, (c, d) in enumerate(zip(claim_ids, doc_ids)):
        c = c[: max_len // 2]
        for w in windows(d, max_len - len(c) - 3, overlap):
            seqs.append(pack(c, w, max_len, cls_id, sep_id))
            owner.append(row)
    return seqs, owner


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="Hub repo or local checkpoint dir")
    ap.add_argument("--splits", nargs="+", default=["dev", "test"])
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--overlap", type=int, default=256, help="tokens shared by neighbouring document windows")
    ap.add_argument("--token-budget", type=int, default=16384)
    ap.add_argument("--max-batch", type=int, default=64)
    ap.add_argument("--limit", type=int, help="first N rows per split (smoke run)")
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    import torch
    from datasets import load_dataset
    from transformers import AutoTokenizer

    token = os.environ.get("HF_TOKEN")
    tok = AutoTokenizer.from_pretrained(args.model, token=token)
    model = load_model(args.model, token=token).to("cuda").eval()
    timing = {"model": args.model, "device": torch.cuda.get_device_name(0)}
    for split in args.splits:
        ds = load_dataset(DATASET, split=split, token=token)
        if args.limit:
            ds = ds.select(range(min(args.limit, len(ds))))
        start = time.perf_counter()
        claims = tok(list(ds["claim"]), add_special_tokens=False)["input_ids"]
        docs = tok(list(ds["doc"]), add_special_tokens=False)["input_ids"]
        seqs, owner = build(claims, docs, args.max_len, args.overlap, tok.cls_token_id, tok.sep_token_id)
        probs = [0.0] * len(seqs)
        batches = make_batches([len(s) for s in seqs], args.token_budget, args.max_batch)
        print(f"{split}: {len(ds)} claims -> {len(seqs)} windows in {len(batches)} batches", flush=True)
        with torch.inference_mode():
            for b, idx in enumerate(batches, 1):
                if b % 200 == 0:  # batches run shortest first, so later ones are slower
                    print(f"  batch {b}/{len(batches)}, {time.perf_counter() - start:.0f}s", flush=True)
                ids, mask = collate([seqs[i] for i in idx], tok.pad_token_id)
                with torch.autocast("cuda", dtype=torch.float16):
                    logits = model(input_ids=ids.cuda(), attention_mask=mask.cuda()).logits.squeeze(-1)
                for i, p in zip(idx, logits.float().sigmoid().tolist()):
                    probs[i] = p
        score, n_windows = [1.0] * len(ds), [0] * len(ds)
        for row, p in zip(owner, probs):
            score[row] = min(score[row], p)
            n_windows[row] += 1
        seconds = time.perf_counter() - start
        with open(args.out / f"aggrefact_{split}.jsonl", "w") as f:
            for i, (name, label) in enumerate(zip(ds["dataset"], ds["label"])):
                f.write(json.dumps({"id": f"{split}-{i}", "dataset": name, "label": 1 - int(label),
                                    "score": round(score[i], 6), "windows": n_windows[i]}) + "\n")
        timing[split] = {"rows": len(ds), "sequences": len(seqs), "seconds": round(seconds, 1),
                         "ms_per_claim": round(1000 * seconds / len(ds), 2)}
        print(split, timing[split], flush=True)
    (args.out / "aggrefact_timing.json").write_text(json.dumps(timing, indent=2) + "\n")


if __name__ == "__main__":
    main()
