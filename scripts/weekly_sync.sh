#!/usr/bin/env bash
#
# weekly_sync.sh — 营销号鉴别数据层 每周同步脚本
#
# 作用：重建版本清单 → git commit → git push
# 前置：gh 已登录（gh auth login），且已 git remote add origin ...
#
# 用法：
#   bash scripts/weekly_sync.sh
#   bash scripts/weekly_sync.sh "2026-07-19 周更：新增AI换脸话术"
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MSG="${1:-auto: weekly data sync $(date +%Y-%m-%d)}"

echo "==> 重建版本清单"
python3 scripts/build_manifest.py

echo "==> 暂存 data/ 与 version.json"
git add data version.json

if git diff --cached --quiet; then
  echo "==> 没有变更，跳过提交"
  exit 0
fi

echo "==> 提交"
git commit -m "$MSG"

echo "==> 推送（需要 gh 已登录）"
if git push; then
  echo "==> 推送成功，所有安装实例将在下次启动热更新"
else
  echo "!! 推送失败：请先 'gh auth login' 并确认 git remote 已配置"
  exit 1
fi
