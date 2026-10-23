"""HTTP API: POST /check scores each sentence of an answer against its sources; GET /health for probes.

The model loads once at startup (lifespan), so the server only accepts requests once it can answer them.
Run with scripts/run.py, or uvicorn api.main:create_app --factory.
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from api.settings import Settings
from hd.detector import Detector


class CheckRequest(BaseModel):
    sources: list[str] = Field(min_length=1, description="Material the answer must be grounded in")
    answer: str = Field(description="Text to check, split into sentences server-side")
    question: str | None = Field(default=None, description="The user question, if the answer replies to one")


class SentenceResult(BaseModel):
    text: str
    start: int
    end: int
    score: float = Field(description="Raw model score, higher = more likely unsupported")
    probability: float = Field(description="Calibrated probability that the sentence is unsupported")
    hallucinated: bool


class CheckResponse(BaseModel):
    model: str
    threshold: float
    hallucinated: int = Field(description="Number of flagged sentences")
    sentences: list[SentenceResult]
    latency_ms: float


def create_app(settings: Settings | None = None, detector: Detector | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.detector = detector or Detector(
            settings.model, token=settings.hf_token, threshold=settings.threshold,
            platt=(settings.platt_a, settings.platt_b), max_len=settings.max_len, threads=settings.threads)
        yield

    app = FastAPI(title="Hallucination detector", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict:
        return {"status": "ok", "model": request.app.state.detector.name}

    @app.post("/check", response_model=CheckResponse)
    def check(body: CheckRequest, request: Request) -> CheckResponse:  # sync: FastAPI runs it in a worker thread
        if len(body.sources) > settings.max_sources:
            raise HTTPException(422, f"at most {settings.max_sources} sources")
        if not any(s.strip() for s in body.sources):
            raise HTTPException(422, "sources are empty")
        if sum(len(s) for s in body.sources) > settings.max_source_chars:
            raise HTTPException(413, f"sources exceed {settings.max_source_chars} characters")
        if len(body.answer) > settings.max_answer_chars:
            raise HTTPException(413, f"answer exceeds {settings.max_answer_chars} characters")
        det = request.app.state.detector
        start = time.perf_counter()
        sentences = det.check(body.sources, body.answer, body.question)
        return CheckResponse(model=det.name, threshold=det.threshold,
                             hallucinated=sum(s["hallucinated"] for s in sentences), sentences=sentences,
                             latency_ms=round(1000 * (time.perf_counter() - start), 1))

    return app
