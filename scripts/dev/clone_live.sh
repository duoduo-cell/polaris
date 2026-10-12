#!/usr/bin/env bash
# 把真实库复制成评测库 polaris_live（清掉对话和记忆），评测和试问答只在这里跑，不碰真实数据。Git Bash 下要关路径转换。
set -euo pipefail
export MSYS_NO_PATHCONV=1
docker exec polaris-db-1 pg_dump -U polaris -Fc -f /tmp/polaris.dump polaris
docker exec polaris-db-1 psql -U polaris -d postgres -c "drop database if exists polaris_live" -c "create database polaris_live"
docker exec polaris-db-1 pg_restore -U polaris -d polaris_live --no-owner /tmp/polaris.dump
docker exec polaris-db-1 psql -U polaris -d polaris_live -c "truncate chat_tasks, chat_messages, chat_threads, memory_events, memory_facts, memory_suppressions restart identity cascade"
