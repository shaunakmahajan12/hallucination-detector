import math

from hd.model import PairEncoder, make_batches, pack
from hd.train import lr_factor, pos_weight, smoke_sample

CLS, SEP = 1, 2


def test_pack_keeps_sentence_and_trims_source_tail():
    seq = pack([10, 11], list(range(100, 120)), max_len=10, cls_id=CLS, sep_id=SEP)
    assert seq == [CLS, 10, 11, SEP, 100, 101, 102, 103, 104, SEP]


def test_pack_short_source_is_untouched():
    assert pack([10], [100, 101], max_len=10, cls_id=CLS, sep_id=SEP) == [CLS, 10, SEP, 100, 101, SEP]


def test_pack_caps_huge_sentence_at_half_the_window():
    seq = pack(list(range(50)), [100, 101], max_len=20, cls_id=CLS, sep_id=SEP)
    assert len(seq) == 15 and seq[-3:] == [100, 101, SEP]


class FakeTok:
    cls_token_id, sep_token_id = CLS, SEP

    def __call__(self, texts, add_special_tokens=False):
        return {"input_ids": [[10 + len(w) for w in t.split()] for t in texts]}


def test_encoder_shares_sources_and_reports_truncation():
    sources = {"a": "x " * 3, "b": "y " * 30}
    rows = [{"source_id": "a", "sentence": "hi there"}, {"source_id": "b", "sentence": "hi"}]
    seqs, cut = PairEncoder(FakeTok(), max_len=16).encode(rows, sources)
    assert len(seqs[0]) == 2 + 3 + 3 and len(seqs[1]) == 16
    assert cut == 0.5


def test_batches_respect_budget_and_cover_every_example():
    lengths = [5, 50, 7, 300, 12, 40, 9, 100] * 5
    for shuffle in (False, True):
        batches = make_batches(lengths, token_budget=320, max_batch=4, shuffle=shuffle, seed=1)
        assert sorted(i for b in batches for i in b) == list(range(len(lengths)))
        assert all(len(b) <= 4 and len(b) * max(lengths[i] for i in b) <= 320 for b in batches)


def test_batch_longer_than_budget_still_gets_its_own_batch():
    assert make_batches([500, 10], token_budget=100, max_batch=8) == [[1], [0]]


def test_lr_warms_up_then_decays_to_zero():
    f = [lr_factor(s, warmup=4, total=20) for s in range(20)]
    assert f[0] == 0.25 and f[3] == 1.0 and f[4] == 1.0
    assert all(a >= b for a, b in zip(f[3:], f[4:])) and f[-1] > 0 and lr_factor(20, 4, 20) == 0.0


def test_pos_weight_modes():
    labels = [1] + [0] * 9
    assert pos_weight(labels, "ratio") == 9.0
    assert math.isclose(pos_weight(labels, "sqrt"), 3.0)
    assert pos_weight(labels, "none") == 1.0


def test_smoke_sample_is_balanced():
    rows = [{"label": i % 5 == 0} for i in range(100)]
    sample = smoke_sample(rows, 10)
    assert len(sample) == 10 and sum(r["label"] for r in sample) == 5
