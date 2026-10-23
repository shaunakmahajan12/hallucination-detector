"""A tiny random-weight ModernBERT classifier with a word-level tokenizer, saved like a real checkpoint.

Tests and the CI container smoke run load this instead of the real model, so they never download weights.
Its scores are meaningless; only shapes and plumbing are exercised.
  python -m hd.tiny_model /path/to/dir
"""

import string
import sys

WORDS = list(string.ascii_lowercase + string.digits) + "the a is was of in to and on at by for it with as".split()


def save(out: str) -> None:
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers
    from transformers import ModernBertConfig, ModernBertForSequenceClassification, PreTrainedTokenizerFast

    specials = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]
    vocab = {w: i for i, w in enumerate(specials + WORDS)}
    backend = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    backend.normalizer = normalizers.Lowercase()
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tok = PreTrainedTokenizerFast(tokenizer_object=backend, pad_token="[PAD]", unk_token="[UNK]", cls_token="[CLS]",
                                  sep_token="[SEP]", mask_token="[MASK]")
    config = ModernBertConfig(vocab_size=len(vocab), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                              num_attention_heads=2, max_position_embeddings=4096, pad_token_id=vocab["[PAD]"],
                              cls_token_id=vocab["[CLS]"], sep_token_id=vocab["[SEP]"], num_labels=1)
    ModernBertForSequenceClassification(config).save_pretrained(out)
    tok.save_pretrained(out)


if __name__ == "__main__":
    save(sys.argv[1])
