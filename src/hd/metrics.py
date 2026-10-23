"""Metrics for the hallucinated class (label 1). Plain Python, so they run and test anywhere."""

import math


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


def calibration(labels: list[int], scores: list[float], bins: int = 10) -> dict:
    """Expected calibration error over equal-width score bins, plus each bin's mean score vs observed rate."""
    groups = [[] for _ in range(bins)]
    for y, s in zip(labels, scores):
        groups[min(bins - 1, int(s * bins))].append((y, s))
    table, ece = [], 0.0
    for i, g in enumerate(groups):
        if not g:
            continue
        conf = sum(s for _, s in g) / len(g)
        rate = sum(y for y, _ in g) / len(g)
        ece += len(g) / len(labels) * abs(conf - rate)
        table.append({"bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}", "n": len(g), "mean_score": round(conf, 4),
                      "hallucinated_rate": round(rate, 4)})
    return {"ece": round(ece, 4), "bins": table}


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)  # avoids exp overflow for very negative z
    return e / (1 + e)


def _logit(s: float) -> float:
    s = min(max(s, 1e-6), 1 - 1e-6)
    return math.log(s / (1 - s))


def platt_fit(labels: list[int], scores: list[float], iters: int = 100) -> tuple[float, float]:
    """Fit p = sigmoid(a * logit(score) + b) by damped Newton on log loss. Monotonic for a > 0, so F1 and AUROC don't
    change; only the probabilities do. Steps are halved until the loss drops, since plain Newton overshoots when
    scores pile up at one end (as the NLI scores do near 1)."""
    xs = [_logit(s) for s in scores]

    def loss(a: float, b: float) -> float:
        total = 0.0
        for x, y in zip(xs, labels):
            p = min(max(_sigmoid(a * x + b), 1e-12), 1 - 1e-12)
            total -= math.log(p) if y else math.log(1 - p)
        return total

    a, b = 1.0, 0.0
    cur = loss(a, b)
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, labels):
            p = _sigmoid(a * x + b)
            w = max(p * (1 - p), 1e-9)
            ga += (p - y) * x
            gb += p - y
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        step = 1.0
        while step > 1e-6 and loss(a - step * da, b - step * db) > cur:
            step /= 2
        if step <= 1e-6:
            break
        a, b = a - step * da, b - step * db
        new = loss(a, b)
        if cur - new < 1e-9 * len(xs):
            break
        cur = new
    return a, b


def platt_apply(scores: list[float], a: float, b: float) -> list[float]:
    return [_sigmoid(a * _logit(s) + b) for s in scores]


def balanced_accuracy(labels: list[int], preds: list[int]) -> float:
    """Mean of recall on each class: LLM-AggreFact's metric, robust to the very different class balance per dataset."""
    pos = [p for y, p in zip(labels, preds) if y]
    neg = [p for y, p in zip(labels, preds) if not y]
    if not pos or not neg:
        return float("nan")
    return (sum(pos) / len(pos) + sum(1 - p for p in neg) / len(neg)) / 2


def best_bacc_threshold(labels: list[int], scores: list[float]) -> float:
    """Threshold t maximizing balanced accuracy when predicting 1 for score >= t."""
    pairs = sorted(zip(scores, labels), reverse=True)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    tp = fp = 0
    best, best_t = -1.0, 1.0
    for i, (s, y) in enumerate(pairs):
        tp += y
        fp += 1 - y
        if i + 1 < len(pairs) and pairs[i + 1][0] == s:
            continue
        bacc = (tp / n_pos + (n_neg - fp) / n_neg) / 2 if n_pos and n_neg else 0.0
        if bacc > best:
            best, best_t = bacc, s
    return best_t
