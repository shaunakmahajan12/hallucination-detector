"""Inference path behind the API: score every sentence of an answer against its sources.

The answer is split with the same splitter as the training data, and sources are formatted the way RAGTruth
formats them, so the model sees inputs shaped like the ones it was trained on. Sources longer than the window are
split into overlapping windows; a sentence is supported if any window supports it (minimum score over windows).

Each sentence gets the raw model score, a calibrated probability (Platt scaling fitted on val) and a verdict at the
val-tuned threshold. Defaults come from results/ft_metrics.json (run 1); tests keep them in sync.
"""

from hd.metrics import platt_apply
from hd.model import build, collate, load_model, make_batches
from hd.sentences import split_sentences

MODEL = "shaunak-12/hallucination-detector-modernbert"
THRESHOLD = 0.650467  # threshold_from_val
PLATT_A, PLATT_B = 0.7123, -0.9041  # calibration_test.platt_from_val


def format_sources(sources: list[str], question: str | None = None) -> str:
    """One source as is; several as "passage i:" blocks; a question first. This mirrors RAGTruth's QA format."""
    body = sources[0] if len(sources) == 1 else "\n\n".join(f"passage {i}:{s}" for i, s in enumerate(sources, 1))
    return f"Question: {question}\n\n{body}" if question else body


class Detector:
    def __init__(self, model: str = MODEL, token: str | None = None, threshold: float = THRESHOLD,
                 platt: tuple[float, float] = (PLATT_A, PLATT_B), max_len: int = 2048, overlap: int = 256,
                 token_budget: int = 16384, max_batch: int = 32, threads: int | None = None):
        import torch
        from transformers import AutoTokenizer

        self.torch = torch
        if threads:
            torch.set_num_threads(threads)
        self.tok = AutoTokenizer.from_pretrained(model, token=token)
        self.model = load_model(model, token=token).eval()
        self.name, self.threshold, self.platt = model, threshold, platt
        self.max_len, self.overlap, self.token_budget, self.max_batch = max_len, overlap, token_budget, max_batch

    def scores(self, seqs: list[list[int]]) -> list[float]:
        out = [0.0] * len(seqs)
        with self.torch.inference_mode():
            for idx in make_batches([len(s) for s in seqs], self.token_budget, self.max_batch):
                ids, mask = collate([seqs[i] for i in idx], self.tok.pad_token_id)
                for i, p in zip(idx, self.model(input_ids=ids, attention_mask=mask).logits.squeeze(-1).sigmoid()
                                .tolist()):
                    out[i] = p
        return out

    def check(self, sources: list[str], answer: str, question: str | None = None) -> list[dict]:
        spans = split_sentences(answer)
        if not spans:
            return []
        sentences = [answer[s:e] for s, e in spans]
        source_ids = self.tok([format_sources(sources, question)], add_special_tokens=False)["input_ids"][0]
        sentence_ids = self.tok(sentences, add_special_tokens=False)["input_ids"]
        seqs, owner = build(sentence_ids, [source_ids] * len(sentences), self.max_len, self.overlap,
                            self.tok.cls_token_id, self.tok.sep_token_id)
        raw = [1.0] * len(sentences)
        for row, p in zip(owner, self.scores(seqs)):
            raw[row] = min(raw[row], p)
        calibrated = platt_apply(raw, *self.platt)
        return [{"text": text, "start": s, "end": e, "score": round(r, 4), "probability": round(c, 4),
                 "hallucinated": r >= self.threshold}
                for text, (s, e), r, c in zip(sentences, spans, raw, calibrated)]
