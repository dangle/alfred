ARG PYTHON_VERSION

FROM python:${PYTHON_VERSION}

ARG PDM_VERSION

ENV UV_LINK_MODE=copy

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
  clang

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN curl -sSL https://pdm-project.org/install-pdm.py \
  | python3 - -v ${PDM_VERSION} -p /usr/local
