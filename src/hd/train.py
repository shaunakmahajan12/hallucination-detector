"""Fine-tune ModernBERT on RAGTruth sentence pairs with a plain PyTorch loop (no HF Trainer).

fp16 autocast + GradScaler (the T4 has no bf16), token-budget batches, gradient accumulation, AdamW with linear
warmup and decay, and a class-weighted BCE loss. The epoch with the best val F1 is kept and scored on test.
Writes results/ft_{val,test}.jsonl in the same format as the NLI baseline, so eval/evaluate.py --method ft works.

Kaggle smoke run (must overfit):  PYTHONPATH=src python -m hd.train --smoke 64 --epochs 15 --lr 1e-4 --accum 1 \
                                  --out /tmp/smoke --ckpt /tmp/smoke/ckpt
Full run:                         PYTHONPATH=src python -m hd.train
"""

import argparse
import json
import math
import time
from pathlib import Path

from hd.data import load_sources, load_split
from hd.metrics import best_threshold, report
from hd.model import MODEL_NAME, PairEncoder, collate, load_model, make_batches


def lr_factor(step: int, warmup: int, total: int) -> float:
    """Linear warmup to 1, then linear decay to 0."""
    if step < warmup:
        return (step + 1) / warmup
    return max(0.0, (total - step) / max(1, total - warmup))


def pos_weight(labels: list[int], mode: str) -> float:
    """BCE weight for the hallucinated class. 'sqrt' softens the full neg/pos ratio (~8 here), which tends to
    push scores toward 1 and hurt calibration; the decision threshold is tuned on val anyway."""
    pos = sum(labels)
    ratio = (len(labels) - pos) / max(pos, 1)
    return {"none": 1.0, "sqrt": math.sqrt(ratio), "ratio": ratio}[mode]


def smoke_sample(rows: list[dict], n: int) -> list[dict]:
    pos = [r for r in rows if r["label"]][: n // 2]
    neg = [r for r in rows if not r["label"]][: n - len(pos)]
    return pos + neg


class Trainer:
    def __init__(self, args):
        import torch
        from transformers import AutoTokenizer

        self.torch, self.args = torch, args
        torch.manual_seed(args.seed)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.amp = self.device == "cuda"
        self.tok = AutoTokenizer.from_pretrained(args.model)
        self.enc = PairEncoder(self.tok, args.max_len)
        self.model = load_model(args.model).to(self.device)
        if args.grad_ckpt:
            self.model.gradient_checkpointing_enable()

    def batches(self, seqs, shuffle, seed=0):
        return make_batches([len(s) for s in seqs], self.args.token_budget, self.args.max_batch, shuffle, seed)

    def logits(self, seqs, idx):
        ids, mask = collate([seqs[i] for i in idx], self.tok.pad_token_id)
        with self.torch.autocast("cuda", dtype=self.torch.float16, enabled=self.amp):
            return self.model(input_ids=ids.to(self.device), attention_mask=mask.to(self.device)).logits.squeeze(-1)

    def predict(self, seqs) -> list[float]:
        self.model.eval()
        out = [0.0] * len(seqs)
        with self.torch.inference_mode():
            for idx in self.batches(seqs, shuffle=False):
                for i, p in zip(idx, self.logits(seqs, idx).float().sigmoid().tolist()):
                    out[i] = p
        return out

    def probe_memory(self):
        """One forward/backward on a budget-filling batch of max-length inputs, so an OOM shows up in seconds."""
        if not self.amp:
            return None
        n = max(1, min(self.args.max_batch, self.args.token_budget // self.args.max_len))
        seqs = [[self.tok.cls_token_id] * self.args.max_len] * n
        self.model.train()
        self.logits(seqs, list(range(n))).float().sum().backward()
        self.model.zero_grad(set_to_none=True)
        peak = self.torch.cuda.max_memory_allocated() / 2**30
        self.torch.cuda.reset_peak_memory_stats()
        return round(peak, 2)

    def fit(self, train_seqs, train_labels, val_seqs, val_labels, ckpt: Path) -> list[dict]:
        torch, a = self.torch, self.args
        n_batches = len(self.batches(train_seqs, shuffle=True))
        total = math.ceil(n_batches / a.accum) * a.epochs
        warmup = max(1, int(a.warmup * total))
        no_decay = [p for n, p in self.model.named_parameters() if p.ndim < 2 or "norm" in n.lower()]
        decay = [p for n, p in self.model.named_parameters() if not (p.ndim < 2 or "norm" in n.lower())]
        opt = torch.optim.AdamW([{"params": decay, "weight_decay": a.weight_decay},
                                 {"params": no_decay, "weight_decay": 0.0}], lr=a.lr)
        sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: lr_factor(s, warmup, total))
        scaler = torch.amp.GradScaler("cuda", enabled=self.amp)
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight(train_labels, a.pos_weight),
                                                                     device=self.device))
        labels = torch.tensor(train_labels, dtype=torch.float)
        print(f"{len(train_seqs)} train examples, {n_batches} batches/epoch, {total} optimizer steps", flush=True)

        log, best_f1, step = [], -1.0, 0
        for epoch in range(a.epochs):
            self.model.train()
            start, tokens, run_loss, run_n = time.perf_counter(), 0, 0.0, 0
            batches = self.batches(train_seqs, shuffle=True, seed=a.seed + epoch)
            for b, idx in enumerate(batches):
                loss = loss_fn(self.logits(train_seqs, idx).float(), labels[idx].to(self.device))
                scaler.scale(loss / a.accum).backward()
                run_loss, run_n = run_loss + loss.item(), run_n + 1
                tokens += sum(len(train_seqs[i]) for i in idx)
                if (b + 1) % a.accum == 0 or b + 1 == len(batches):
                    scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    scaler.step(opt)
                    scaler.update()
                    opt.zero_grad(set_to_none=True)
                    sched.step()
                    step += 1
                    if step % a.log_every == 0:
                        secs = time.perf_counter() - start
                        eta = secs / (b + 1) * (len(batches) - b - 1) / 60
                        print(f"epoch {epoch} step {step}/{total} loss {run_loss / run_n:.4f} "
                              f"lr {sched.get_last_lr()[0]:.2e} {tokens / secs:.0f} tok/s, epoch eta {eta:.0f} min",
                              flush=True)
                        run_loss, run_n = 0.0, 0
            train_secs = time.perf_counter() - start
            scores = self.predict(val_seqs)
            t = best_threshold(val_labels, scores)
            entry = {"epoch": epoch, "train_minutes": round(train_secs / 60, 1),
                     "train_tok_per_s": round(tokens / train_secs), "threshold": round(t, 6),
                     "val": report(val_labels, scores, t)}
            if self.amp:
                entry["peak_gpu_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
            print(json.dumps(entry), flush=True)
            log.append(entry)
            if entry["val"]["f1"] > best_f1:
                best_f1 = entry["val"]["f1"]
                self.model.save_pretrained(ckpt)
                self.tok.save_pretrained(ckpt)
        return log


def write_preds(path: Path, rows: list[dict], scores: list[float]) -> None:
    with open(path, "w") as f:
        for r, s in zip(rows, scores):
            f.write(json.dumps({"id": r["id"], "task_type": r["task_type"], "model": r["model"],
                                "label": r["label"], "score": round(s, 6)}) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL_NAME)
    ap.add_argument("--max-len", type=int, default=2048)
    ap.add_argument("--token-budget", type=int, default=8192, help="max padded tokens per micro-batch")
    ap.add_argument("--max-batch", type=int, default=32)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--warmup", type=float, default=0.06, help="fraction of optimizer steps")
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--pos-weight", choices=["none", "sqrt", "ratio"], default="sqrt")
    ap.add_argument("--grad-ckpt", action="store_true")
    ap.add_argument("--limit", type=int, help="use only the first N train and val rows")
    ap.add_argument("--smoke", type=int, help="train and validate on the same N balanced train rows (must overfit)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=100)
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--ckpt", type=Path, default=Path("checkpoints/ft"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sources = load_sources()
    if args.smoke:
        train = val = smoke_sample(load_split("train"), args.smoke)
    else:
        train, val = load_split("train", limit=args.limit), load_split("val", limit=args.limit)
    tr = Trainer(args)
    train_seqs, train_cut = tr.enc.encode(train, sources)
    val_seqs, _ = tr.enc.encode(val, sources)
    info = {"config": {k: str(v) for k, v in vars(args).items()},
            "device": tr.torch.cuda.get_device_name(0) if tr.amp else "cpu",
            "train_truncated_frac": round(train_cut, 4), "probe_peak_gpu_gb": tr.probe_memory()}
    print(json.dumps(info), flush=True)

    info["epochs"] = tr.fit(train_seqs, [r["label"] for r in train], val_seqs, [r["label"] for r in val], args.ckpt)
    tr.model = load_model(args.ckpt).to(tr.device)  # best epoch by val F1
    write_preds(args.out / "ft_val.jsonl", val, tr.predict(val_seqs))
    if not args.smoke:
        test = load_split("test")
        test_seqs, _ = tr.enc.encode(test, sources)
        start = time.perf_counter()
        scores = tr.predict(test_seqs)
        info["test_ms_per_sentence"] = round(1000 * (time.perf_counter() - start) / len(test), 2)
        write_preds(args.out / "ft_test.jsonl", test, scores)
    (args.out / "ft_train_log.json").write_text(json.dumps(info, indent=2) + "\n")


if __name__ == "__main__":
    main()
