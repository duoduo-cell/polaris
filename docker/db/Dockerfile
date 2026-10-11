# 北极星的数据库：Postgres 17 + pgvector（官方 pgvector 镜像）+ ParadeDB pg_search 0.22.6（BM25 关键词检索，带中文分词）。
# Docker Hub 上的 paradedb/paradedb 镜像在本机拉不下来（2026-10-11 两次卡在第一层），所以在已有的 pgvector 镜像上
# 装 ParadeDB 在 GitHub Releases 发布的 Debian 包（AGPL-3.0，只作为数据库扩展使用、不改它的代码）。
# 构建：scripts/build_db_image.sh（先把包下载到 data/vendor/pg_search/，不进仓库）。
FROM pgvector/pgvector:pg17
COPY postgresql-17-pg-search_0.22.6-1PARADEDB-bookworm_amd64.deb /tmp/pg_search.deb
RUN dpkg -i /tmp/pg_search.deb && rm /tmp/pg_search.deb
CMD ["postgres", "-c", "shared_preload_libraries=pg_search"]
