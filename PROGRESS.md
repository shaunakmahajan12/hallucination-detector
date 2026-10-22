# Progress

Plan: `~/.claude/plans/scope-the-new-hallucination-temporal-peacock.md`.
Restarted 2026-10-21 after the first version was deleted. It used too much disk and RAM on the 8 GB Mac.

## Rules for this machine (M2, 8 GB RAM, ~15 GB free disk)
- The Mac only holds code and small data (raw + processed RAGTruth come to ~80 MB, both gitignored).
- Training, the NLI baseline and evaluation run on Kaggle (free T4). Nothing is downloaded into `~/.cache/huggingface` locally.
- Trained weights go to the Hugging Face Hub, never into this folder.

## Step 1: data (done 2026-10-21)
`python3.11 data/prepare.py` (stdlib only, ~2 s) writes `data/processed/`:
- `sources.jsonl` holds each source once. Sentence rows reference it by `source_id`, which keeps the files at ~45 MB
  instead of copying the source onto every sentence.
- 173 non-"good" responses dropped (144 incorrect refusals, 29 truncated).
- Validation = 10% of train sources (hashed `source_id`); the script asserts no source appears in two splits.

| split | sentences | hallucinated |
|---|---|---|
| train | 100,028 | 11.1% |
| val | 11,713 | 11.1% |
| test | 18,965 | 8.2% |

Data2txt has the most hallucinations (~14% of train sentences) and Summary the fewest (~7%).
Source length: median ~320 words, p95 ~900, max ~1,750 (roughly 2,300 tokens).
So a 2,048-token limit covers almost every source, and ModernBERT's 8k window isn't needed.

## Step 2: zero-shot NLI baseline (done 2026-10-22)
- `src/hd/baselines.py`: `cross-encoder/nli-deberta-v3-base`. The source is cut into 250-word windows with 50-word
  overlap, and score = 1 - max P(entailment) across windows. About 2.2 windows per sentence (~42k pairs on test).
- `eval/evaluate.py` recomputes every metric from saved predictions: threshold from val F1, then test overall, by
  task and by LLM. `src/hd/metrics.py` is plain Python and unit-tested.
- Run it on Kaggle: `notebooks/nli_baseline_kaggle.ipynb` (built by `scripts/build_kaggle_notebook.py`, which embeds
  the code). Download `results.zip` into `results/`. Estimated ~10 min on a T4.

Local disk cleanup (2026-10-21): deleted the old MLX Llama models and the first version's ModernBERT/NLI weights from
`~/.cache/huggingface` (9.1 GB -> 2.0 GB). The Mac went from 15 GB to 22 GB free.

Results (`results/nli_*`, Kaggle T4, 16 min total; recomputed locally from the saved predictions and they match):
- Threshold from val F1 = 0.9946. Most faithful sentences still score near 1 because the model rarely gives
  high entailment to paraphrases, so only a tiny score range separates the classes.
- Test: F1 0.197, precision 0.117, recall 0.630, AUROC 0.640 (8.2% positives, so an all-positive guess gets F1 ~0.15).
- By task: QA is best (AUROC 0.75), Data2txt worst (0.59). Structured JSON sources don't suit NLI.
- By LLM: GPT-3.5/GPT-4 F1 ~0.04 (only 105 hallucinated sentences between them); Mistral best at F1 0.32.
- The notebook builder now writes empty files with `!touch`, because `%%writefile` rejects an empty cell.

The old Kaggle results were not reused; a fresh run was quicker than checking the old code.

## Step 2b: Gemini judge (494/1000 done 2026-10-22, paused on the free daily quota)
- `src/hd/judge.py`, stdlib only (REST through urllib, nothing to install). Model `gemini-3.5-flash-lite` with
  `thinkingLevel: minimal`: ~0.8 s and ~10 output tokens per call, free tier; paid would be $0.30 in / $2.50 out per
  1M tokens. Gemini 2.0 Flash from the plan was shut down 2026-06-01. `gemini-3.8-flash` was tried first: 30-110 s
  per call, repeated 503 "high demand", and a 429 quota stop after a few calls, so it was dropped (its 2 rows were
  set aside, not used).
- Fixed sample: 1,000 test sentences (seed 0), 70 hallucinated. One call per sentence with the same source + sentence
  the detector sees, temperature 0, JSON schema answer {supported, hallucinated}. Rate limited (default 10 rpm),
  retries 429/5xx for ~8 min, appends each answer to `results/<tag>_test.jsonl` (default tag `gemini`) and resumes
  after a quota stop.
- Key: `GEMINI_API_KEY` in `.env` (gitignored). Run: `PYTHONPATH=src python3.11 -m hd.judge` (~100 min at 10 rpm),
  then `python3.11 eval/evaluate.py --judge gemini`. That scores every other method with results on exactly the same
  1,000 sentences (at its own val threshold) and reports latency and cost per 1,000 checks.
- Self-Refine has no `RateLimiter`/`safe_generate` (the plan's path doesn't exist), so a small one was written here.
- Free tier = 500 requests/day/model (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), reset at midnight Pacific
  (~3 a.m. Eastern). Stopped at 494 on 2026-10-22. To finish: rerun `PYTHONPATH=src python3.11 -m hd.judge` on
  2026-10-23 (reaches 994, then stops on the quota) and once more on 2026-10-24 for the last 6. Same model, settings
  and sample, so the rows stay comparable. `results/gemini_test.jsonl` is uncommitted until complete.
- Idea for later: Bespoke-MiniCheck-7B on Kaggle (4-bit) as a specialist-checker baseline.

## Step 3: fine-tuned ModernBERT (run 1 done 2026-10-22)
Run 1 (2 epochs, lr 3e-5, max_len 2048, pos_weight sqrt): best epoch = 1. Results in `results/ft_*`, recomputed
locally from the saved predictions and they match Kaggle's.

| | F1 | precision | recall | AUROC |
|---|---|---|---|---|
| val, epoch 0 | 0.638 | 0.654 | 0.623 | 0.907 |
| val, epoch 1 | 0.671 | 0.712 | 0.635 | 0.917 |
| **test** (threshold 0.650 from val) | **0.652** | 0.653 | 0.651 | **0.916** |
| test, NLI baseline | 0.197 | 0.117 | 0.630 | 0.640 |

- Test is close to val (F1 -0.02, AUROC equal), so no sign of overfitting to val or a split shift.
- By task: Data2txt F1 0.718 (NLI's worst task is now the best), QA 0.589, Summary 0.446 (recall only 0.35).
- By LLM: Mistral 0.722 and Llama-2 0.62-0.69; GPT-3.5 0.368 and GPT-4 0.454, but those rest on 58 and 47
  hallucinated sentences, so they're noisy.
- ~108 min/epoch at ~9,900 tok/s, peak GPU 8 GB, test inference 21.6 ms/sentence on the T4.
- Checkpoint is only in that Kaggle run's output (`checkpoints/ft/`); not yet on the HF Hub.

- `src/hd/model.py`: input is `[CLS] sentence [SEP] source [SEP]`, capped at 2,048 tokens. The sentence goes first, so
  truncation only trims the source's tail. Each source is tokenized once. Batches are grouped by length under a padded
  token budget (8,192 per micro-batch) instead of a fixed batch size.
- `src/hd/train.py`: plain PyTorch loop. fp16 autocast + GradScaler, gradient accumulation 4, AdamW (lr 3e-5, no decay
  on norms/biases), 6% linear warmup then linear decay, grad clip 1.0, BCE with pos_weight = sqrt(neg/pos) ~2.8
  (the full ratio ~8 would hurt calibration). 2 epochs; the epoch with the best val F1 is saved and scored on test.
  At startup it runs one forward/backward on a worst-case batch, so an out-of-memory error shows up in seconds.
- Writes `results/ft_{val,test}.jsonl` + `ft_train_log.json` (per-epoch val metrics, tok/s, peak GPU memory,
  truncation rate); `eval/evaluate.py --method ft` gives the same tables as the baseline.
- Run it on Kaggle: `notebooks/train_kaggle.ipynb`. The first cell after setup is the smoke run (64 balanced examples,
  train = val), and the notebook stops if val F1 < 0.9. Start it with *Save & Run All (Commit)* so it runs in the
  background. Pass bar from the plan: val F1 must beat NLI's 0.248.
- No torch on the Mac by design; `tests/test_model_and_train.py` covers packing, batching, LR schedule, class weights.
  Tests run with `uvx --python 3.11 pytest`.
- Not done yet: pushing the checkpoint to the HF Hub (needs an HF token as a Kaggle secret).
