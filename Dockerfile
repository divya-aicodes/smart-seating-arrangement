FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
RUN chmod 0555 /app/docker-entrypoint.sh /app/docker-worker-entrypoint.sh \
    && mkdir -p /app/instance \
    && chown app:app /app/instance

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; request = urllib.request.Request('http://127.0.0.1:8000/api/v1/health', headers={'Host': os.environ['HEALTHCHECK_HOST']}); urllib.request.urlopen(request, timeout=3)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
