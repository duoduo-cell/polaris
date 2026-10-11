#!/usr/bin/env bash
# 构建北极星的数据库镜像（Postgres 17 + pgvector + pg_search），见 docker/db/Dockerfile。
set -euo pipefail
cd "$(dirname "$0")/.."
VER=0.22.6
DEB="postgresql-17-pg-search_${VER}-1PARADEDB-bookworm_amd64.deb"
mkdir -p data/vendor/pg_search
if [ ! -s "data/vendor/pg_search/$DEB" ]; then
  curl -L --fail -o "data/vendor/pg_search/$DEB" "https://github.com/paradedb/paradedb/releases/download/v${VER}/${DEB}"
fi
docker compose build db
