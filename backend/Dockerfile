# One image, three entrypoints (api, worker, migrate) - see docker-compose.yml.
# Rebuild this image when requirements.txt (or the base Python image) changes.
# App Python is bind-mounted in compose with uvicorn --reload — do not rebuild for .py edits.
# Avoid `# syntax=docker/dockerfile:1` on Desktop: the BuildKit frontend gRPC can drop mid-build.

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/poultech

# Dependencies first, so a code change does not reinstall the world.
COPY requirements.txt ./
# Progress visible so slow installs don't look hung; retry once on transient network errors.
RUN pip install --default-timeout=120 -r requirements.txt

COPY . .

# Never run as root: a request-handling bug should not be a container escape.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /srv/poultech
USER appuser

# Private keys are mounted (or read from your secret store), never baked into an image.
ENV JWT_PRIVATE_KEY_PATH=/run/secrets/jwt_private.pem \
    JWT_PUBLIC_KEY_PATH=/run/secrets/jwt_public.pem

EXPOSE 8000

# /health/live answers even when Postgres is down - that is the point of splitting
# liveness from readiness, a failing check here would restart the pod forever.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
