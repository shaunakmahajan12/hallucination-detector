# hallucination-detector

A sentence-level hallucination detector for RAG answers: given source passages and one answer sentence, predict
whether the sentence is supported. Trained on RAGTruth in PyTorch and compared against zero-shot NLI and an LLM judge.

Work in progress. See `PROGRESS.md`.

```bash
# put RAGTruth's response.jsonl and source_info.jsonl in data/raw/, then
python data/prepare.py
```
