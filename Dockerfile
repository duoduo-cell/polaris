FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.4

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

# 先装依赖再拷代码，改代码时不用重装依赖
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
COPY eval/fixtures ./eval/fixtures
RUN uv sync --frozen --no-dev

EXPOSE 8000
# 启动时先把表结构迁移到最新，再起服务
CMD ["sh", "-c", "alembic upgrade head && uvicorn polaris.api.app:app --host 0.0.0.0 --port 8000"]
