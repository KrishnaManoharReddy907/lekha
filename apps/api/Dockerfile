# Lekha API (FastAPI + background worker in one process; OCR in a small process pool)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# libgl/glib are needed by OpenCV (used by the OCR engine); nothing else at system level.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.9
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
# Warm the OCR models at build time so the first upload is not slow.
RUN .venv/bin/python -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()"
# Two malloc arenas instead of one per thread: far less heap bloat from worker threads on a 512 MB host.
ENV PATH="/app/.venv/bin:$PATH" LEKHA_ENV=production MALLOC_ARENA_MAX=2
EXPOSE 8000
CMD ["sh", "-c", "python -m lekha.cli migrate && uvicorn lekha.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
