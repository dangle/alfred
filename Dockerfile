FROM python:3.12-slim-bookworm

ARG PDM_VERSION=2.16.1

RUN apt-get update \
  && export DEBIAN_FRONTEND=noninteractive \
  && apt-get install -y \
  curl
RUN curl -sSL https://pdm-project.org/install-pdm.py \
  | python3 - -v ${PDM_VERSION} -p /usr/local

WORKDIR /app

COPY . .

RUN pdm install

CMD [ "pdm", "run", "/app/src/alfred/main.py"]
