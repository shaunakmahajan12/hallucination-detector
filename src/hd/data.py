"""Load the processed RAGTruth files written by data/prepare.py."""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "processed"


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f]


def load_sources(data_dir: Path = DATA) -> dict[str, str]:
    return {s["source_id"]: s["text"] for s in read_jsonl(Path(data_dir) / "sources.jsonl")}


def load_split(split: str, data_dir: Path = DATA, limit: int | None = None) -> list[dict]:
    rows = read_jsonl(Path(data_dir) / f"{split}.jsonl")
    return rows[:limit] if limit else rows


def chunk_words(text: str, size: int = 250, overlap: int | None = None) -> list[str]:
    """Split a long source into overlapping word windows that fit a 512-token encoder alongside the sentence."""
    overlap = size // 5 if overlap is None else overlap
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    words = text.split()
    if len(words) <= size:
        return [" ".join(words)]
    step = size - overlap
    return [" ".join(words[i : i + size]) for i in range(0, len(words) - overlap, step)]
