# ReDA 운영 이미지: Hermes Agent 이미지 위에 ReDA(reda, reda-mcp)를 얹는다.
#   docker build -f deploy/Dockerfile --build-arg HERMES_IMAGE=<hermes 이미지> -t reda:<git sha> .
# HERMES_IMAGE는 서버에 이미 있는 Hermes 이미지를 ID나 태그로 고정해서 넘긴다 (latest 금지).
ARG HERMES_IMAGE
# uv는 기반 이미지마다 위치가 달라서(Hostinger 이미지 등) 공식 이미지에서 버전을 고정해 가져온다
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM ${HERMES_IMAGE}

USER root
WORKDIR /opt/reda
COPY pyproject.toml uv.lock README.md CLAUDE.md ./
COPY src ./src
COPY distribution ./distribution
COPY deploy/run-poll.sh deploy/run-schedule-bot.sh ./deploy/
COPY --from=uv /uv /usr/local/bin/reda-uv

# 의존성은 uv.lock 그대로(--frozen), 개발용 패키지는 넣지 않는다. Python 3.13은 uv가 받아 /opt/reda/python에 둔다.
RUN set -eux; \
    UV=/usr/local/bin/reda-uv; \
    UV_PROJECT_ENVIRONMENT=/opt/reda/.venv UV_CACHE_DIR=/tmp/uv-cache UV_PYTHON_INSTALL_DIR=/opt/reda/python \
        "$UV" sync --frozen --no-dev --no-editable --python 3.13; \
    rm -rf /tmp/uv-cache; \
    chmod -R a+rX /opt/reda; chmod a+x /opt/reda/deploy/*.sh; \
    /opt/reda/.venv/bin/reda --help >/dev/null

ENV PATH="/opt/reda/.venv/bin:${PATH}"
# 시작 명령은 compose.yml이 정한다: s6 /init(gateway·대시보드를 띄움)을 쓰지 않고 서비스 계정 uid로 ReDA 스크립트만 실행
