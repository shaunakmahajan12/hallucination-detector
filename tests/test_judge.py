import json
import sys
from pathlib import Path

import pytest

from hd import judge
from hd.judge import Judge, RateLimiter, parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from evaluate import judge_report  # noqa: E402


def response(verdict, thought=False):
    parts = [{"text": "thinking...", "thought": True}] if thought else []
    parts.append({"text": json.dumps({"verdict": verdict})})
    return {"candidates": [{"content": {"parts": parts}}],
            "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 8, "thoughtsTokenCount": 40}}


def test_parse_ignores_thought_parts_and_counts_thinking_as_output():
    assert parse(response("hallucinated", thought=True)) == (1, {"input": 500, "output": 48})
    assert parse(response("supported"))[0] == 0


def test_parse_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        parse(response("maybe"))


def test_rate_limiter_sleeps_once_window_is_full(monkeypatch):
    clock, slept = [100.0], []
    monkeypatch.setattr(judge.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(judge.time, "sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    limiter = RateLimiter(rpm=2)
    limiter.wait()
    limiter.wait()
    assert not slept
    limiter.wait()
    assert len(slept) == 1 and 59 < slept[0] < 61


def test_judge_retries_malformed_answer(monkeypatch):
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    answers = iter([{"candidates": []}, response("hallucinated")])
    j = Judge("m", "key", rpm=1000)
    monkeypatch.setattr(j, "post", lambda body: next(answers))
    label, usage, secs = j("source", "sentence")
    assert label == 1 and usage["input"] == 500 and secs >= 0


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_judge_report_compares_methods_on_the_same_sentences(tmp_path):
    base = [{"id": str(i), "task_type": "QA", "model": "m", "label": i % 2} for i in range(6)]
    write_jsonl(tmp_path / "gemini_test.jsonl", [{**r, "score": float(r["label"]), "llm": "g", "seconds": 1.0,
                                                  "input": 1000, "output": 100} for r in base[:4]])
    write_jsonl(tmp_path / "nli_test.jsonl", [{**r, "score": 0.9 if r["id"] in "05" else 0.1} for r in base])
    (tmp_path / "nli_metrics.json").write_text(json.dumps({"threshold_from_val": 0.5}))
    out = judge_report("gemini", tmp_path)
    assert out["sample"] == 4 and out["methods"]["gemini"]["f1"] == 1.0
    assert out["methods"]["nli"]["n"] == 4  # only the judged ids, not all 6
    assert out["usd_per_1000_checks_paid_tier"] == round(1000 * (1000 * 0.75 + 100 * 3.75) / 1e6, 3)
