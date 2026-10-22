"""ModernBERT pair classifier: [CLS] sentence [SEP] source [SEP] -> one logit, higher means more likely hallucinated.

The sentence goes first, so truncation only ever trims the tail of the source and never the claim being checked.
Packing and batching are plain Python (testable without torch); torch is imported only where tensors are built.
"""

import random

MODEL_NAME = "answerdotai/ModernBERT-base"


def pack(sent_ids: list[int], src_ids: list[int], max_len: int, cls_id: int, sep_id: int) -> list[int]:
    sent_ids = sent_ids[: max_len // 2]  # guard against a pathological sentence crowding out the whole source
    room = max_len - len(sent_ids) - 3
    return [cls_id, *sent_ids, sep_id, *src_ids[:room], sep_id]


class PairEncoder:
    """Token ids for (sentence, source) rows. Each source is tokenized once and shared by all its sentences."""

    def __init__(self, tokenizer, max_len: int = 2048):
        self.tok = tokenizer
        self.max_len = max_len

    def _ids(self, texts: list[str]) -> list[list[int]]:
        return self.tok(texts, add_special_tokens=False)["input_ids"]

    def encode(self, rows: list[dict], sources: dict[str, str]) -> tuple[list[list[int]], float]:
        """Return (packed ids per row, fraction of rows whose source was truncated)."""
        sids = sorted({r["source_id"] for r in rows})
        src = dict(zip(sids, self._ids([sources[s] for s in sids])))
        sents = self._ids([r["sentence"] for r in rows])
        seqs, cut = [], 0
        for r, s in zip(rows, sents):
            seq = pack(s, src[r["source_id"]], self.max_len, self.tok.cls_token_id, self.tok.sep_token_id)
            cut += len(seq) < min(len(s), self.max_len // 2) + len(src[r["source_id"]]) + 3
            seqs.append(seq)
        return seqs, cut / max(len(rows), 1)


def make_batches(lengths: list[int], token_budget: int, max_batch: int, shuffle: bool = False,
                 seed: int = 0) -> list[list[int]]:
    """Group example indices so each padded batch (size * longest) stays within token_budget.

    Similar lengths are grouped to cut padding. With shuffle, examples are sorted inside random pools of ~100
    batches and the batch order is shuffled, so each epoch sees different neighbours without sorting the whole set.
    """
    idx = list(range(len(lengths)))
    rng = random.Random(seed)
    if shuffle:
        rng.shuffle(idx)
        pool = 100 * max_batch
        idx = [i for p in range(0, len(idx), pool) for i in sorted(idx[p : p + pool], key=lambda i: lengths[i])]
    else:
        idx.sort(key=lambda i: lengths[i])
    batches, cur, longest = [], [], 0
    for i in idx:
        new_longest = max(longest, lengths[i])
        if cur and (len(cur) + 1 > max_batch or (len(cur) + 1) * new_longest > token_budget):
            batches.append(cur)
            cur, new_longest = [], lengths[i]
        cur.append(i)
        longest = new_longest
    if cur:
        batches.append(cur)
    if shuffle:
        rng.shuffle(batches)
    return batches


def collate(seqs: list[list[int]], pad_id: int):
    import torch

    width = max(len(s) for s in seqs)
    ids = torch.full((len(seqs), width), pad_id, dtype=torch.long)
    mask = torch.zeros((len(seqs), width), dtype=torch.long)
    for i, s in enumerate(seqs):
        ids[i, : len(s)] = torch.tensor(s)
        mask[i, : len(s)] = 1
    return ids, mask


def load_model(name: str = MODEL_NAME):
    from transformers import AutoConfig, AutoModelForSequenceClassification

    config = AutoConfig.from_pretrained(name, num_labels=1)
    if hasattr(config, "reference_compile"):  # ModernBERT would torch.compile and recompile for every batch shape
        config.reference_compile = False
    return AutoModelForSequenceClassification.from_pretrained(name, config=config, attn_implementation="sdpa")
