#!/usr/bin/env bash
#
# clean_push.sh — 照妖镜 · 干净推送脚本
#
# 作用：将当前所有更改 squash 成 1 条干净的 commit，然后 force push。
#       确保 GitHub 页面不会暴露内部 commit 消息（case编号、操作细节等）。
#
# 前置：
#   - 当前目录是 git 仓库
#   - remote "origin" 已配置且可推送
#
# 用法：
#   bash scripts/clean_push.sh
#
# ⚠️ 注意：此脚本会 force push 到 origin/main！
# ⚠️ commit 消息永远固定为干净默认，不接受任何参数（防止暴露内部细节）。
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 固定 commit 消息：绝不暴露内部细节（忽略任何传入参数）
DEFAULT_MSG="Initial commit: 照妖镜 · Scam Slayer"
MSG="$DEFAULT_MSG"

echo "==> [1/4] 检查工作区状态..."
if git diff --quiet && [ -z "$(git status --porcelain)" ]; then
    echo "     无需推送（工作区干净）"
    exit 0
fi

echo "==> [2/4] 创建 orphan 分支并 squash 所有更改..."
git checkout --orphan _clean_push_temp 2>/dev/null || true
git add -A
# 排除 .workbuddy/ 和 pending_samples.md 等本地文件（由 .gitignore 控制）
# 注：git add -A 偶发不尊重 .gitignore（曾把 *.bak 带进 commit），故对已知敏感路径
# 做显式 unstage 双保险；references/collected_data 含用户提交反馈队列(PII)，绝不进仓库。
git reset HEAD -- .workbuddy/ pending_samples.md *.csv quality_report.json form.html \
  references/collected_data collected_data '*.bak' 'training_report*.md' \
  data/case-library.md references/case-library.md '**/case-library.md' 2>/dev/null || true
git commit -m "$MSG"

echo "==> [3/4] Force push 到 origin/main..."
git push --force origin _clean_push_temp:main

echo "==> [4/4] 清理临时分支，同步本地 main..."
git checkout main 2>/dev/null || git checkout -b main
git reset --hard origin/main
git branch -D _clean_push_temp 2>/dev/null || true

echo ""
echo "✅ 干净推送完成。GitHub 现在仅显示 1 条 commit："
echo "   $MSG"
echo ""
echo "   总文件数: $(git ls-files | wc -l | tr -d ' ')"
echo "   Commit:    $(git log --oneline -1)"
