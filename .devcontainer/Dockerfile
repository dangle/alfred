FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ARG PDM_VERSION=2.19.1

RUN curl -sSL https://pdm-project.org/install-pdm.py \
  | python3 - -v ${PDM_VERSION} -p /usr/local
