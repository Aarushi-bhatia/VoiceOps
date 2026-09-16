FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.8" \
        "pydantic-settings>=2.4" "sqlalchemy>=2.0.30" aiosqlite greenlet \
        "redis>=5.0" httpx "asyncpg>=0.29"

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 1001 voiceops && chown -R voiceops /srv
USER voiceops

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/dev/null"]
