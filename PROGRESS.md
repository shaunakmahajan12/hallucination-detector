# Progress

Plan: `~/.claude/plans/scope-the-new-hallucination-temporal-peacock.md`.
Restarted 2026-10-21 after the first version was deleted. It used too much disk and RAM on the 8 GB Mac.

## Resume here (paused 2026-10-23)
Done: data, NLI baseline, fine-tuned ModernBERT run 1 (test F1 0.652 / AUROC 0.916, on the Hub), calibration,
error analysis, LLM-AggreFact (OOD BAcc 70.2 dev-tuned), API + Docker + CI (green). Open, in order:
1. Gemini judge: 494/1000 in `results/gemini_test.jsonl` (uncommitted until complete). Rerun
   `PYTHONPATH=src python3.11 -m hd.judge` after the daily quota resets (~3 a.m. Eastern); it needs two more days.
   Then `python3.11 eval/evaluate.py --judge gemini` and commit.
2. Local run of the real model + CPU latency/cost vs Gemini: start Colima (profile `outreach`), and either a read-only
   HF token on the Mac or make the Hub model public (user to decide).
3. README with results tables, reliability plot and limitations (label noise, Summary weakness, per-domain thresholds).
4. Resume integration (plan's last section), after the CPU latency number exists (user's call, 2026-10-23).
   Draft bullets agreed in chat, every number from `results/`:
   - Fine-tuned ModernBERT-base (150M) with a custom PyTorch loop (fp16 autocast, gradient accumulation,
     class-weighted loss, token-budget batching) on 100K RAGTruth sentence pairs to flag unsupported claims in RAG
     answers; test F1 0.65 / AUROC 0.92 vs 0.20 / 0.64 for a zero-shot NLI baseline.
   - Evaluated out of domain on LLM-AggreFact (11 datasets, 29K claims): 71.5 balanced accuracy vs 75.0-75.6 for
     0.4-0.8B specialist checkers; Platt-calibrated outputs (ECE 0.043 -> 0.016); error analysis traced weak spots
     to summarization and label noise.
   - Served per-sentence calibrated scores via FastAPI `/check` in a non-root CPU Docker image, with GitHub Actions
     CI (unit tests + container smoke test on a tiny random-weight model). Add the CPU latency / cost-vs-Gemini figure.
   Leaderboard comparison is loose (protocol unstated); keep the "half of false alarms are unannotated" point out
   (20-example sample).

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
- Checkpoint on the HF Hub (private): `shaunak-12/hallucination-detector-modernbert`, with `results/ft_metrics.json`
  and `ft_train_log.json`. Pushed 2026-10-22 by `notebooks/upload_hub_kaggle.ipynb` (CPU, reads the `HF_TOKEN` Kaggle
  secret, takes the training run's output as input).

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

## Step 4: evaluation (in progress 2026-10-22)
Calibration (`eval/evaluate.py` now adds `calibration_test`: ECE over 10 bins, raw and after Platt scaling fitted on
val; Platt is monotonic so F1/AUROC don't change):
- Fine-tuned: test ECE 0.043 raw -> 0.016 with Platt (a 0.71, b -0.90). Raw scores are overconfident at the top
  (0.9-1.0 bin: mean score 0.97, observed rate 0.81), as expected from pos_weight ~2.8.
- NLI: ECE 0.640 raw -> 0.030 with Platt, but only because Platt flattens it to roughly the base rate everywhere.
  Low ECE isn't usefulness; its AUROC is still 0.64.

Error analysis (`python eval/errors.py --method ft` -> `results/ft_error_breakdown.json`, `ft_errors_sample.md`;
write-up in `results/ft_error_analysis.md`):
- Summary's low recall is ranking, not threshold: a Summary-only threshold lifts its F1 0.446 -> 0.464 only, and its
  misses are confident (scores 0.01-0.33). Main misses: invented plausible facts and subtle detail conflicts.
- About half of the 20 sampled false alarms are real unsupported content that annotators didn't mark, so measured
  precision understates the model.
- Decision: run 2 (more epochs / heavier class weight) parked; if Summary matters later, try ModernBERT-large or adding
  the previous sentence as context. Hours arithmetic and citation checks fit a rule layer in the product.

LLM-AggreFact (run 2026-10-22, `results/aggrefact_*`): `src/hd/aggrefact.py` scored dev + test (59,740 claims,
~26 ms/claim on a T4; 2.4% of claims needed more than one 2,048-token window, up to 21; score = min over windows). Metrics from
`python3.11 eval/evaluate.py --aggrefact`, balanced accuracy (BAcc):

| | AUROC | BAcc, RAGTruth threshold (strict) | BAcc, threshold tuned per dataset on AggreFact dev |
|---|---|---|---|
| 10 out-of-domain datasets | 0.767 | 66.1 | 70.2 |
| all 11 (incl. RAGTruth) | 0.781 | 67.4 | 71.5 |
| RAGTruth subset alone | 0.921 | 80.3 | 84.6 |

- Leaderboard (llm-aggrefact.github.io, all 11): FactCG-DeBERTa-L (0.4B) 75.6, MiniCheck-Flan-T5-L (0.8B) 75.0,
  Bespoke-MiniCheck-7B 77.4. Ours at 0.15B: 71.5 with dev-tuned thresholds. Its threshold protocol isn't stated, so
  compare loosely. Per dataset vs FactCG: ahead on RAGTruth (84.6 vs 82.3) and WiCE (77.5 vs 74.2), level on
  FactCheck-GPT and ExpertQA, behind on AggreFact-CNN (57 vs 70), REVEAL (78 vs 88) and the TofuEval sets.
- Best dev thresholds range from 0.015 to 0.90 across datasets, so the scores don't transfer across domains on one
  threshold: a product needs per-domain calibration, or training data beyond RAGTruth.
- Weakest: AggreFact-CNN (AUROC 0.59) and ExpertQA (0.61; every leaderboard model is near 59 there).

Still to do: CPU latency/cost vs Gemini, reliability plot for the README,
Gemini comparison once the judge finishes.

## Step 5: API, Docker, CI (done 2026-10-23; CI green on GitHub)
- `api/main.py`: FastAPI app factory. `POST /check {sources[], answer, question?}` returns one result per answer
  sentence: text, character offsets, raw score, calibrated probability (Platt from val) and `hallucinated` at the
  val threshold. `GET /health`. Model loads once at startup. Request limits in `api/settings.py` (pydantic-settings,
  `HD_*` env vars; `HF_TOKEN` for the private Hub model).
- `src/hd/detector.py` is the inference path: same sentence splitter as training (moved to `hd.sentences`;
  `prepare.py` output verified byte-identical), sources formatted like RAGTruth (`passage i:`, `Question:`), long
  sources windowed (min over windows; `hd.model.windows/build`, shared with the AggreFact run).
- Docker: `python:3.11-slim-bookworm`, CPU-only torch 2.14.1, non-root uid 10001, HEALTHCHECK on /health, model
  pulled from the Hub at startup (not baked in). `docker build --target test .` runs the whole suite inside.
- Tests: API tests use `hd.tiny_model` (random 2-layer ModernBERT, word-level tokenizer) so nothing downloads; they
  skip on the Mac (no torch by design) and run in CI. `tests/test_detector.py` keeps the threshold/Platt defaults in
  sync with `results/ft_metrics.json`.
- CI (`.github/workflows/ci.yml`): pytest with CPU torch, plus `scripts/container_smoke.py` (build, tiny model in a
  volume, /health, one /check). Fixed on the way: `windows()` produced nothing when overlap >= window (every
  sentence came back as score 1.0); root-written tiny model files were 0600, unreadable by the app user.
- Pushing workflow files needed `gh auth refresh -s workflow` (done). The GitHub repo is public.

Next: run the real model locally (Colima profile `outreach` exists but is stopped; needs a read token as HF_TOKEN)
and measure CPU latency/cost per 1,000 checks vs Gemini; reliability plot; README; Gemini comparison when the judge
finishes (rerun `PYTHONPATH=src python3.11 -m hd.judge` on 2026-10-23 and 2026-10-24).
