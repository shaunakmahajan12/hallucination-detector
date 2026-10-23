import math

from hd.baselines import score_rows
from hd.data import chunk_words
from hd.metrics import (auroc, balanced_accuracy, best_bacc_threshold, best_threshold, calibration, platt_apply,
                        platt_fit, prf, report)


def test_chunk_words_short_text_is_one_chunk():
    assert chunk_words("a b c", size=5) == ["a b c"]


def test_chunk_words_overlaps_and_covers_the_end():
    words = [str(i) for i in range(12)]
    chunks = chunk_words(" ".join(words), size=5, overlap=2)
    assert chunks[0].split() == words[:5]
    assert chunks[1].split()[:2] == words[3:5]
    assert chunks[-1].split()[-1] == "11"


def test_score_rows_takes_best_chunk_per_sentence():
    sources = {"s1": " ".join(["x"] * 10) + " " + " ".join(["fact"] * 10)}
    rows = [{"source_id": "s1", "sentence": "fact"}, {"source_id": "s1", "sentence": "invented"}]

    def fake_entailment(pairs):  # entailed only when the chunk mentions the sentence
        return [0.9 if h in p else 0.1 for p, h in pairs]

    scores, n_pairs = score_rows(rows, sources, fake_entailment, chunk_size=8)
    assert n_pairs == 2 * len(chunk_words(sources["s1"], 8))
    assert [round(s, 2) for s in scores] == [0.1, 0.9]


def test_prf():
    assert prf([1, 1, 0, 0], [1, 0, 1, 0]) == {"precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_auroc_perfect_random_and_ties():
    assert auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert auroc([0, 1], [0.5, 0.5]) == 0.5
    assert math.isnan(auroc([1, 1], [0.1, 0.2]))


def test_best_threshold_separates_classes():
    labels, scores = [0, 0, 1, 1], [0.1, 0.3, 0.6, 0.9]
    t = best_threshold(labels, scores)
    assert t == 0.6
    assert report(labels, scores, t)["f1"] == 1.0


def test_calibration_perfect_and_overconfident():
    labels = [0] * 5 + [1] * 5
    assert calibration(labels, [0.05] * 5 + [0.95] * 5)["ece"] == 0.05
    assert calibration([0, 0, 0, 1], [0.95] * 4)["ece"] == 0.7


def test_platt_fixes_scale_and_keeps_order():
    import random

    rng = random.Random(0)
    true_p = [rng.random() for _ in range(4000)]
    labels = [int(rng.random() < p) for p in true_p]
    squashed = [p**0.25 for p in true_p]  # overconfident toward 1
    a, b = platt_fit(labels, squashed)
    fixed = platt_apply(squashed, a, b)
    assert calibration(labels, fixed)["ece"] < calibration(labels, squashed)["ece"] / 3
    order = sorted(range(len(fixed)), key=lambda i: squashed[i])
    assert all(fixed[i] <= fixed[j] for i, j in zip(order, order[1:]))


def test_balanced_accuracy_ignores_class_balance():
    labels = [1] + [0] * 99
    assert balanced_accuracy(labels, [0] * 100) == 0.5  # all-negative scores 99% accuracy but 0.5 balanced
    assert balanced_accuracy(labels, [1] + [0] * 99) == 1.0


def test_best_bacc_threshold():
    labels, scores = [0, 0, 0, 1], [0.1, 0.2, 0.7, 0.8]
    t = best_bacc_threshold(labels, scores)
    assert t == 0.8 and balanced_accuracy(labels, [int(s >= t) for s in scores]) == 1.0
