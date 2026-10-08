# The application and worker use the same immutable, Arch-based image. A real
# Arch package requires makepkg/pacman tooling, which is supplied by
# base-devel. The worker's Bubblewrap sandbox is still required for every
# untrusted build; this image does not grant it a Docker socket or host mounts.
FROM archlinux:base-devel AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/flexy/backend

RUN pacman -Syu --noconfirm --needed \
        bubblewrap ca-certificates python python-pip python-virtualenv xz zstd \
    && pacman -Scc --noconfirm \
    && groupadd --gid 10001 flexy \
    && useradd --uid 10001 --gid flexy --create-home --shell /usr/bin/nologin flexy \
    && python -m venv /opt/flexy-venv

COPY backend/requirements.txt ./requirements.txt
# The runtime sets PYTHONDONTWRITEBYTECODE; keep install layers free of
# timestamp-bearing .pyc files as well.
RUN /opt/flexy-venv/bin/pip install --no-cache-dir --no-compile -r requirements.txt

# Keep the complete backend source (including migrations and tests) together so
# the same image can be used for local verification. Runtime processes import
# only app/ and have no write access to this source tree.
COPY backend ./
COPY fixtures /srv/flexy/fixtures

RUN mkdir -p /var/lib/flexy/uploads /var/lib/flexy/artifacts /var/lib/flexy/work \
    && chown -R flexy:flexy /srv/flexy /var/lib/flexy

USER 10001:10001

ENV PATH=/opt/flexy-venv/bin:$PATH \
    UPLOAD_DIR=/var/lib/flexy/uploads \
    ARTIFACT_DIR=/var/lib/flexy/artifacts \
    WORK_DIR=/var/lib/flexy/work \
    FLEXY_RECIPE_DIR=/srv/flexy/fixtures/recipes \
    BWRAP_PATH=/usr/bin/bwrap

EXPOSE 8000

FROM runtime AS test

USER root
COPY backend/requirements.txt backend/requirements-dev.txt /tmp/flexy-requirements/
RUN /opt/flexy-venv/bin/pip install --no-cache-dir --no-compile -r /tmp/flexy-requirements/requirements-dev.txt
USER 10001:10001
