# Native amd64/arm64 control-plane image for safe inspection and queue jobs.
# This is NOT an Arch conversion environment. Keep conversion workers on the
# separate Arch image only after their host and sandbox have been validated.
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BUILD_EXECUTOR=unavailable

WORKDIR /srv/flexy/backend

RUN groupadd --gid 10001 flexy \
    && useradd --uid 10001 --gid flexy --create-home --shell /usr/sbin/nologin flexy \
    && python -m venv /opt/flexy-venv

COPY backend/requirements.txt ./requirements.txt
RUN /opt/flexy-venv/bin/pip install --no-cache-dir --no-compile -r requirements.txt

COPY backend ./
COPY fixtures /srv/flexy/fixtures

RUN mkdir -p /var/lib/flexy/uploads /var/lib/flexy/artifacts /var/lib/flexy/work \
    && chown -R flexy:flexy /var/lib/flexy

USER 10001:10001

ENV PATH=/opt/flexy-venv/bin:$PATH \
    UPLOAD_DIR=/var/lib/flexy/uploads \
    ARTIFACT_DIR=/var/lib/flexy/artifacts \
    WORK_DIR=/var/lib/flexy/work \
    FLEXY_RECIPE_DIR=/srv/flexy/fixtures/recipes

EXPOSE 8000

FROM runtime AS test
USER root
COPY backend/requirements.txt backend/requirements-dev.txt /tmp/flexy-requirements/
RUN /opt/flexy-venv/bin/pip install --no-cache-dir --no-compile -r /tmp/flexy-requirements/requirements-dev.txt
USER 10001:10001
