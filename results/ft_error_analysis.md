# Error analysis: fine-tuned ModernBERT (run 1) on RAGTruth test

Inputs: `results/ft_error_breakdown.json` (counts over all 18,965 test sentences) and `results/ft_errors_sample.md`
(a fixed sample of 30 missed hallucinations and 20 false alarms, 10 and about 7 per task, seed 0, read by hand with the
annotators' spans and notes). Both come from `python eval/errors.py --method ft`. Threshold 0.650 (from val).

## What the counts say

- **Hallucination type barely matters.** Recall is 0.66 for both Evident Conflict and Evident Baseless Info and
  0.63 for Subtle Baseless Info. Subtle Conflict is 0.27, but it has only 15 test sentences.
- **Spans spilling over a sentence boundary are not a real source of noise.** Only 10 of 1,561 positives overlap
  their span by 1-2 characters.
- **Data2txt null-field hallucinations are easy** (`due_to_null`, recall 0.92). Claims that are true in the real world
  but absent from the source are harder (`implicit_true`, 0.56 vs 0.66).
- **Summary is a ranking problem, not a threshold problem.** The model flags only 1.2% of supported Summary sentences
  (Data2txt 4.4%), so it is conservative there. But a Summary-specific threshold tuned on val only lifts Summary F1
  from 0.446 to 0.464 (overall F1 unchanged, 0.651). Summary AUROC is 0.81 vs 0.90-0.95 for the other tasks, and the
  sampled Summary misses score 0.009-0.33: confident misses, not near-misses.

## Missed hallucinations (30)

| Failure type | Count | Examples |
|---|---|---|
| Invented but plausible detail (new fact, review opinion, speculation) | 12 | "second test on October 11th", "the bathrooms are clean", "producer who worked with Michael Jackson", "service can be slow" |
| Subtle conflict on a specific detail (number, date, role, body part) | 7 | "lost her foot" (leg), "18 points and 10 rebounds" (10 points in the first half), "trial is underway" (expected within five years), victim vs suspect as the former student |
| Arithmetic over structured data (opening hours) | 3 | "weekdays 11:30-7:30, weekends 11:30-8:00" against a 7-day hours table |
| Label covers the whole response, sentence alone looks fine | 3 | "Here's an example of how the truncated cone might look", a bare list item "* Rye berries" |
| Wrong citation ("(passage 3)" when it was passage 2) | 2 | |
| Unsupported conclusion or claim of absence | 3 | "Therefore, the answer would be: cycling", "the Italian meaning is not stated" (it is) |

By task: Data2txt misses are mostly claims about the reviews that are invented or contradict them (7/10), plus hours
arithmetic (3/10). QA misses are
dominated by annotation granularity and citations (5/10). Summary misses split evenly between invented plausible
facts (5) and subtle detail conflicts (5).

## False alarms (20)

| Verdict after reading the source | Count | Examples |
|---|---|---|
| **Model is right**: the sentence has content the source doesn't support, but annotators didn't mark it | 9 | "parchment paper or aluminum foil" (passages say spray the sheet), "many Germans speak English fluently", "expected to survive after surgery", "good ambiance" (Ambience is null) |
| Borderline (mild extrapolation) | 3 | "craft beer" from "grab a beer", "4.0 stars on Yelp" |
| Genuine false alarm: the source supports it | 8 | correct opening hours, "accepts reservations" (true), a correct casualty count, a settlement split that matches |

Close to half of the sampled false alarms are unannotated hallucinations. So the reported precision (0.65) understates
the model, and the RAGTruth labels are an imperfect ceiling. The README should say so, but only as an observation from
this 20-sentence sample, not as a corrected number.

## What this means for next steps

1. **A second run with more epochs or a heavier class weight is unlikely to fix Summary.** The misses are confident,
   and catching them means checking every detail against a long article, or noticing a plausible addition the
   article never mentions. Better candidates, if Summary matters: a larger encoder (ModernBERT-large), or giving the
   model the previous sentence as context.
2. **Hours arithmetic and citation checks are better done by rules than by the model** (parse the hours table;
   check that a cited passage contains the claim). That's a cheap product-layer addition, not a training change.
3. **Response-level spans in QA can't be learned at sentence level.** Report this as a dataset limitation.
4. **Label noise caps measured precision.** Worth one honest line in the README, using this sample.
