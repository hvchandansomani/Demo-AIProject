# ---------- stage 1: build wheels (compilers stay out of the final image)
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- stage 2: runtime
FROM python:3.12-slim
ARG APP_VERSION=1.0.0
ARG GIT_COMMIT=unknown
# Standard image labels: `docker inspect` (or the ECR console) shows which
# build and commit produced this image.
LABEL org.opencontainers.image.title="Doubt Wall" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    TRUST_PROXY=true

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser wsgi.py .

# Kubernetes runs this with runAsNonRoot, runAsUser 10001 and a read-only root
# filesystem (/tmp is an emptyDir), so keep the UID and avoid writing elsewhere.
USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"

# One worker, many threads: Prometheus metrics, rate limits and chaos state live
# in process memory, so one process keeps them consistent. Scale with replicas.
# (Doubts themselves are in PostgreSQL, so every replica shows the same wall.)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "60", "--access-logfile", "-", "wsgi:app"]
