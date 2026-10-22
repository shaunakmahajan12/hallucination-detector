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

## Next: step 2, zero-shot NLI baseline (on Kaggle)
