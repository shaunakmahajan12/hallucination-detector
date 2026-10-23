# CPU-only image for the API. The model is not baked in: it downloads from the Hugging Face Hub at startup
# (public repo, no token needed; mount a volume on the cache to keep it across restarts).
FROM python:3.11-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/src
WORKDIR /app
COPY requirements-api.txt ./
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.1 \
    && pip install --no-cache-dir -r requirements-api.txt
COPY src/ ./src/
COPY api/ ./api/
COPY scripts/run.py ./scripts/

# docker build --target test .   runs the whole suite, including the API tests that need torch
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN python -m pytest -q

FROM base AS runtime
ENV HD_HOST=0.0.0.0 HF_HOME=/home/app/.cache/huggingface
RUN useradd --uid 10001 --create-home app && mkdir -p /home/app/.cache/huggingface && chown -R app:app /home/app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "scripts/run.py"]
