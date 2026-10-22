"""Metrics for the hallucinated class (label 1). Plain Python, so they run and test anywhere."""


def prf(labels: list[int], preds: list[int]) -> dict:
    tp = sum(1 for y, p in zip(labels, preds) if y and p)
    fp = sum(1 for y, p in zip(labels, preds) if not y and p)
    fn = sum(1 for y, p in zip(labels, preds) if y and not p)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def best_threshold(labels: list[int], scores: list[float]) -> float:
    """Threshold t maximizing F1 when predicting hallucinated for score >= t."""
    pairs = sorted(zip(scores, labels), reverse=True)
    positives = sum(labels)
    tp = fp = 0
    best_f1, best_t = -1.0, 1.0
    for i, (s, y) in enumerate(pairs):
        tp += y
        fp += 1 - y
        if i + 1 < len(pairs) and pairs[i + 1][0] == s:
            continue  # only cut between distinct scores
        f1 = 2 * tp / (tp + fp + positives) if positives else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, s
    return best_t


def auroc(labels: list[int], scores: list[float]) -> float:
    """Area under the ROC curve via the Mann-Whitney rank statistic, with tied scores sharing an average rank."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum = sum(r for r, y in zip(ranks, labels) if y)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def report(labels: list[int], scores: list[float], threshold: float) -> dict:
    out = prf(labels, [int(s >= threshold) for s in scores])
    out["auroc"] = auroc(labels, scores)
    out = {k: round(v, 4) for k, v in out.items()}
    out.update(n=len(labels), hallucinated=sum(labels))
    return out
