"""Sentence splitting shared by data preparation and the API, so the API checks exactly the units the model was
trained on. Standard library only."""

import re

# A sentence ends at . ! or ? (optionally followed by a closing quote or bracket) and whitespace, or at a newline.
SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+|\n+")
# Initials, common abbreviations and list numbers shouldn't end a sentence ("Dr. Smith", "J. K. Rowling", "1. Open").
NO_SPLIT_BEFORE = re.compile(r"(\b[A-Z]|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|e\.g|i\.e|No|Inc|Ltd|Co|U\.S)|^\s*\d{1,2})\.$")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character offsets of each sentence, so annotated spans can be mapped onto them."""
    spans, start = [], 0
    for m in SENTENCE_END.finditer(text):
        if m.group().strip() == "" and NO_SPLIT_BEFORE.search(text[start : m.start()]):
            continue
        spans.append((start, m.start() + len(m.group().rstrip())))
        start = m.end()
    spans.append((start, len(text)))
    # Trim surrounding whitespace and drop empty pieces.
    out = []
    for s, e in spans:
        piece = text[s:e]
        if piece.strip():
            lead = len(piece) - len(piece.lstrip())
            out.append((s + lead, s + len(piece.rstrip())))
    return out
