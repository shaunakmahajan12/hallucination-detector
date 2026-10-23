"""API tests against a tiny random model (hd.tiny_model). Need torch, so they run in Docker and CI, not on the Mac."""

import pytest

pytest.importorskip("torch")
pytest.importorskip("fastapi")
pytest.importorskip("pydantic_settings")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from api.settings import Settings  # noqa: E402
from hd import tiny_model  # noqa: E402

SOURCE = "the cat sat on the mat in the sun"


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("tiny-model")
    tiny_model.save(str(path))
    return str(path)


def client(model_dir, **overrides):
    return TestClient(create_app(Settings(_env_file=None, model=model_dir, **overrides)))


def test_health(model_dir):
    with client(model_dir) as c:
        assert c.get("/health").json() == {"status": "ok", "model": model_dir}


def test_multi_sentence_answer_gets_one_result_per_sentence(model_dir):
    answer = "The cat sat on the mat. It was in the sun! Was it 9?"
    with client(model_dir) as c:
        body = c.post("/check", json={"sources": [SOURCE], "answer": answer}).json()
    assert [s["text"] for s in body["sentences"]] == ["The cat sat on the mat.", "It was in the sun!", "Was it 9?"]
    for s in body["sentences"]:
        assert answer[s["start"] : s["end"]] == s["text"]
        assert 0 <= s["probability"] <= 1 and s["hallucinated"] == (s["score"] >= body["threshold"])
    assert body["hallucinated"] == sum(s["hallucinated"] for s in body["sentences"])


def test_empty_answer_returns_no_sentences(model_dir):
    with client(model_dir) as c:
        body = c.post("/check", json={"sources": [SOURCE], "answer": "   "}).json()
    assert body["sentences"] == [] and body["hallucinated"] == 0


def test_long_source_is_windowed(model_dir):
    long_source = " ".join(["the cat sat on the mat"] * 100)  # ~600 tokens against a 64-token window
    with client(model_dir, max_len=64) as c:
        det = c.app.state.detector
        seen = []
        original = det.scores
        det.scores = lambda seqs: seen.append(len(seqs)) or original(seqs)
        body = c.post("/check", json={"sources": [long_source], "question": "where", "answer": "It sat. It ran."})
    assert len(body.json()["sentences"]) == 2
    assert seen and seen[0] > 2 and det.max_len == 64


def test_several_sources_and_a_question(model_dir):
    with client(model_dir) as c:
        r = c.post("/check", json={"sources": ["the cat", "the mat"], "question": "what?", "answer": "A cat."})
    assert r.status_code == 200 and len(r.json()["sentences"]) == 1


@pytest.mark.parametrize("payload,status", [
    ({"sources": [], "answer": "x."}, 422),
    ({"sources": ["  "], "answer": "x."}, 422),
    ({"sources": ["a"] * 21, "answer": "x."}, 422),
    ({"sources": ["a"], "answer": "x" * 20_001}, 413),
    ({"sources": ["a" * 200_001], "answer": "x."}, 413),
])
def test_rejects_bad_requests(model_dir, payload, status):
    with client(model_dir) as c:
        assert c.post("/check", json=payload).status_code == status
